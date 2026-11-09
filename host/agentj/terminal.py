"""Paired-phone line terminal; security contract in agentjarvis/ARCHITECTURE.md (P408)."""
from __future__ import annotations

import asyncio
import codecs
import contextlib
import json
import os
import re
import secrets
import shlex
import shutil
import signal
import socket
import sys
import time

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from . import controls, passkey, term_store as store, term_vault as vault, wire
from . import term_mac
from .term_askpass import NO_PYTHON, TOKEN_ENV, Helper, SudoDesk, in_session, safe_python
from .term_bang import BangDesk, line_problem
from .term_vshare import VshareDesk
from .term_pty import CAP as PTY_CAP, PtyDesk, VtMasker, password_mode, pick_shell as pty_shell, size_ok
from .term_mask import Masker
from .term_shell import (HIST_SETUP, Term, _sha, checked_sudo, classify, local_ok, platform_ok, scrub_history, service_keys,
                         shell_env)

TOOL = "AgentJTerminal"            # the host card for `enable` / `allow` (approval_level.HOST_CARDS, never auto-allowed)
PHONE_TYPES = ("term_open", "term_line", "term_keys", "term_close", "term_forget", "term_sudo_ok", "term_sudo_no")
PHONE_TYPES += ("vault_share",)     # P414 (§37.11): term_vshare.py
PHONE_TYPES += ("term_in", "term_resize", "term_confirm_no")   # 0.26.9 (§38.15): PTY keys / size / 「取消」 (term_pty.py)
PHONE_TYPES += ("term_prefill",)
ESTOP_OK = ("term_close", "term_forget")
NONCE_TTL, CONFIRM_TTL, PENDING_MS, CARD_TTL = 120, 120, 180000, 300
HIDDEN_S, KILL_GRACE, CAST_DAYS = 60, 3.0, 30
RECONNECT_S, REPLAY_CHARS = 30, 128 * 1024
LINE_MAX, FRAME_CHARS, RATE = 4096, 4000, 64 * 1024
UNRECORDED = "[输入给正在运行的程序，未记录]"
_HEX32 = re.compile(r"[0-9a-f]{32}")



def _readonly(line: str) -> bool:
    """H1 execution proof: closed literal-output subset, never historical danger.readonly."""
    from .term_direct import literal_output
    return literal_output(line)


def line_needs_face(line: str) -> bool:
    """Terminal text is never classified for UV; listed Agent J executors own it."""
    return False


class Desk(SudoDesk, BangDesk, VshareDesk, PtyDesk):
    """serve's glue (`h` = serve.Host): phone messages, `terminal_cfg`, the host info block, close on e-stop / stop.
    P414: BangDesk (term_bang.py: 「!」, transcript, terminal_go / terminal_read), VshareDesk (term_vshare.py)."""

    def __init__(self, host):
        self.h = host
        self.term: Term | None = None
        self.opening: str | None = None      # the device whose open is past step 2 (busy)
        self.opens: dict[str, dict] = {}      # host nonce → step-1 record
        self.clock, self.wall = time.monotonic, time.time
        self.sudo = None                      # tests: a stand-in sudo (else checked_sudo())
        self.os_ok = None                     # tests: force the platform answer
        self.proc = "/proc"
        self.python: str | None = None       # R3 (H-1): the helper's system interpreter ("" = none; tests may set it)
        self.shell: str | None = None        # P437: "bash" / "zsh" (None = this platform's: zsh on macOS, bash elsewhere)
        self.pty_ok = None                   # 0.26.9: tests may force the PTY answer (None = term_pty.pty_ok())
        self.pty_shell: str | None = None    # 0.26.9: tests may pin the PTY shell (None = the owner's login shell)
        self.watch: asyncio.Task | None = None
        self._bang_init()
        self._vshare_init()

    @property
    def st(self):
        return self.h.st

    def supported(self) -> bool:
        return platform_ok() if self.os_ok is None else bool(self.os_ok)

    def sudo_path(self) -> str | None:
        return self.sudo or checked_sudo()

    @staticmethod
    def _scrub(path, shell: str) -> int:
        """#184: bash's file through scrub_history; zsh's metafied one byte for byte (P437, term_mac)."""
        return term_mac.scrub_history(path, True) if shell == "zsh" else scrub_history(path)

    def _log(self, action, device, result, obj=None, **kw):
        with contextlib.suppress(Exception):
            controls.log(self.st, action=action, device=device, result=result, obj=obj, **kw)

    def h_paired(self, s):
        """0.30.0 hotfix (董事长 11-09): the login / vault-share card is asked from a background task spawned by this
        session's signed + UV-checked term_open. 0.30.0's elevate.request only accepts the paired frame's own task, so the
        card was refused (authority_changed → 「已取消」) on every phone. Re-bind the same paired authority to this task:
        the verifier is the same live check as the frame's (this session still ready, allowed, a phone)."""
        from . import provenance
        return provenance.paired_request(lambda: self.h._paired_live(s))

    def _notice(self, zh: str, en: str) -> None:
        with contextlib.suppress(Exception):
            self.h.agent_notice(f"{zh} / {en}")

    def _prefs(self) -> None:
        with contextlib.suppress(Exception):
            self.h._post(self.h._send_ready, lambda s: self.h.preferences_msg(s))

    # ------------------------------------------------------------ host info block (§38)
    def info(self, device: str | None) -> dict:
        cfg = store.read_config(self.st)
        e = store.entry(cfg, device) if device else None
        if e and "until" in e and e["until"] <= self.wall():
            e = None
        m = store.meta(self.st, device) if device and e and "until" not in e else None
        t = self.term
        mode = m["mode"] if m else ("session" if t and t.device == device and t.pw else None)
        out = {"on": cfg["on"], "os": self.supported(), "me": bool(e), "sudo": mode,
               "web_ok": bool(e) and (e["web"] or not store.is_web(self.st, device)), "minutes": cfg["minutes"],
               "idle_s": store.IDLE_S, "open": t is not None,
               "pk": bool(device) and self.st.passkey_of_device(device) is not None,   # P417 #185: 「先设置 Face ID」
               "kind": store.device_kind(self.st, device), "mobile": True, "resume": True, "prefill": True,
               "modes": ["line", "pty"] if self.pty_supported() else ["line"]}   # 0.26.9 (§38.15): cap term_pty1
        why = self.pty_blocked()
        if why:                                  # w2-sec H-3: the page says why the full terminal is not offered
            out["pty_why"] = why
        from .olares_terminal import external_url
        url = external_url()
        if url:
            out["external_url"] = url
        if e and "until" in e:
            out["until"] = e["until"]
        return out

    def vault_info(self, device: str | None) -> dict:
        """P414 (§37.9): this session's `vault` host block."""
        return vault.info(self.st, device)

    # ------------------------------------------------------------ phone messages
    async def on_phone(self, s, obj: dict) -> None:
        t = obj.get("t")
        if self.h.stopped() and t not in ESTOP_OK:
            if t in ("term_open", "vault_share") and wire.is_rid(obj.get("r")):
                await self._res(s, obj["r"], t, False, why="estop")
            if t == "term_prefill":
                await self.h.send_app(s, {"t": "term_prefill_res", "id": obj.get("id"), "seq": obj.get("seq"),
                                          "ok": False, "why": "estop"})
            self.st.log("control_refused", device=s.device, action=t, reason="estop")
            return
        if t == "vault_share":
            return await self.on_vshare(s, obj)
        if t == "term_open":
            return await self.on_open(s, obj)
        if t == "term_forget":
            return await self.on_forget(s, obj)
        x = self.term
        if x is None or obj.get("id") != x.id or x.closing:
            if t == "term_prefill":
                await self.h.send_app(s, {"t": "term_prefill_res", "id": obj.get("id"), "seq": obj.get("seq"),
                                          "ok": False, "why": "not_open"})
            self.st.log("control_refused", device=s.device, action=t, reason="not_open")
            return
        if x.disconnected_at is not None or (s.device == x.device and x.s is not s):
            if t == "term_prefill":
                await self.h.send_app(s, {"t": "term_prefill_res", "id": x.id, "seq": obj.get("seq"),
                                          "ok": False, "why": "disconnected"})
            self.refused(s.device, t, "disconnected")
            return
        if t == "term_prefill":
            return await self.on_prefill(x, s, obj)
        if t == "term_in":                                   # 0.26.9 (§B): only from a device on this session's writer list
            if s.device not in x.writers:
                self.refused(s.device, t, "not_writer")      # w2-sec L-4: once per minute, not once per frame
                return
            return await self.on_term_in(x, s, obj)
        if s.device != x.device:
            self.st.log("control_refused", device=s.device, action=t, reason="not_open")
            return
        if t == "term_resize":
            return await self.on_resize(x, s, obj)
        if t == "term_confirm_no":                           # 0.26.9: 「取消」 on a confirm — a held PTY Enter is erased now
            n = obj.get("n")
            if isinstance(n, str) and x.kind == "pty" and x.hold == "confirm" and n == x.hold_n:
                self._pty_cancel(x, "owner")
            elif isinstance(n, str):
                x.confirms.pop(n, None)
            return
        if t == "term_line":
            return await self.on_line(x, s, obj)
        if t == "term_keys":
            if obj.get("key") in ("int", "quit") and x.kind == "pty":    # 0.26.9: the line discipline sends the signal
                return await self.on_term_in(x, s, {"d": "\x03" if obj["key"] == "int" else "\x1c"})
            if obj.get("key") in ("int", "quit"):
                await asyncio.to_thread(self._signal_children, x, signal.SIGINT if obj["key"] == "int" else signal.SIGQUIT)
            return
        if t == "term_close":                                       # the page hidden 60 s says so; anything else = 「结束」
            return await self.close("background" if obj.get("why") == "background" else "owner")
        if t in ("term_sudo_ok", "term_sudo_no"):
            return await self.on_sudo_answer(x, s, obj)

    async def _res(self, s, r, action, ok, **kw) -> None:
        with contextlib.suppress(Exception):
            await self.h.send_app(s, {"t": "ctl_res", "r": r, "action": action, "ok": ok, **kw})

    def _refusal(self, s, enable: bool = False, resume=None):
        a = getattr(self.h, "assist", None)
        if a is not None and getattr(a, "terminal", None) is not None and a.terminal.desk is not None:
            return "busy", {}
        if self.h.sessions.get(s.cid) is not s or s.state != "ready" or not self.st.is_allowed(s.pub):
            return "device_not_allowed", {}
        if not self.supported():
            return "os", {}
        kind = store.device_kind(self.st, s.device)
        if enable:                            # #375: phone/tablet use the same UV + computer-password contract
            if kind not in ("desktop", "phone"):
                return "device_type", {}
            if not self.st.passkey_of_device(s.device):
                return "no_passkey", {}
            if self.term is not None or self.opening:
                return "busy", {}
            if self.h.stopped():
                return "estop", {}
            left = store.locked_for(self.st, s.device)
            return ("locked", {"secs": left}) if left else (None, {})
        listed = store.entry(store.read_config(self.st), s.device) is not None
        if listed and not self.st.passkey_of_device(s.device):
            store.prune(self.st)              # the passkey was removed: its entry and cache go (said once as no_passkey)
            return "no_passkey", {}
        cfg = store.prune(self.st)
        e = store.entry(cfg, s.device)
        if not cfg["on"]:
            return "disabled", {}
        if e is None:
            return "device_not_allowed", {}
        if store.is_web(self.st, s.device) and not e["web"]:
            return "web_not_allowed", {}
        if not self.st.passkey_of_device(s.device):
            return "no_passkey", {}
        if resume is not None:
            x = self.term
            if x is None or x.closing or x.kind != "pty" or x.id != resume or x.device != s.device:
                return "not_open", {}
            if x.disconnected_at is None:
                return "busy", {}
            if self.clock() - x.disconnected_at >= RECONNECT_S or self.clock() >= x.expires or self.clock() - x.last >= store.IDLE_S:
                return "stale", {}
        elif self.term is not None or self.opening:
            return "busy", {}
        if self.h.stopped():
            return "estop", {}
        left = store.locked_for(self.st, s.device)
        if left:
            return "locked", {"secs": left}
        return None, {}

    async def on_open(self, s, obj: dict) -> None:
        r = obj.get("r")
        if not wire.is_rid(r):
            return
        mode = obj.get("mode", "line")       # P417 #185: "enable" = switch on for this remote, then open
        if mode not in ("line", "enable", "pty"):
            return await self._res(s, r, "term_open", False, why="shape")
        resume = obj.get("resume")
        if "resume" in obj and (mode != "pty" or not isinstance(resume, str) or not re.fullmatch(r"[0-9a-f]{16}", resume)):
            return await self._res(s, r, "term_open", False, why="shape")
        size = (obj.get("cols"), obj.get("rows"))     # 0.26.9 (§38.15): pty needs the window size (enable + size = pty)
        if mode == "pty" and not self.pty_supported():
            return await self._res(s, r, "term_open", False, why="fence_dev" if self.pty_blocked() else "os")
        if mode == "pty" and not size_ok(*size):
            return await self._res(s, r, "term_open", False, why="shape")
        why, extra = self._refusal(s, mode == "enable", resume)
        if why:
            self._log("term_open", s.device, "refused:" + why, {"mode": mode})
            return await self._res(s, r, "term_open", False, why=why, **extra)
        pk = self.st.passkey_of_device(s.device)
        target = {"mode": mode, **({"resume": resume} if resume is not None else {})}
        if "n" not in obj and "fa" not in obj:                       # step 1: the host issues the nonce (L5)
            now = self.clock()
            self.opens = {k: v for k, v in self.opens.items() if now - v["at"] < NONCE_TTL}
            if len(self.opens) > 16:
                return await self._res(s, r, "term_open", False, why="busy")
            n = secrets.token_hex(16)
            m = store.meta(self.st, s.device)
            salt = m["salt"] if m and m["mode"] == "prf" else secrets.token_bytes(32)
            vn = secrets.token_bytes(32) if m and m["mode"] == "prf" and m["cred"] == pk["id"] and mode in ("line", "pty") else None   # P414 (M3)
            priv = X25519PrivateKey.generate()
            from .elevate import _raw_pub
            self.opens[n] = {"device": s.device, "r": r, "at": now, "salt": salt, "priv": priv, "vn": vn, "mode": mode}
            if resume is not None:
                self.opens[n]["resume"] = resume
            if self.pty_supported() and size_ok(*size) and mode in ("pty", "enable"):
                self.opens[n]["size"] = size
            ch = passkey.elevate_challenge(self.h.channel, s.device, n, "term_open", n, controls.object_digest("term_open", target))
            return await self._res(s, r, "term_open", False, why="passkey_required", n=n, fa=pk["id"], fa_s=passkey.pk_state(pk), fa_ch=wire.b64u(ch),
                                   salt=wire.b64u(salt), epk=wire.b64u(_raw_pub(priv)), **({"vn": wire.b64u(vn)} if vn else {}))
        n = obj.get("n")
        pend = self.opens.pop(n, None) if isinstance(n, str) else None
        if pend is None or pend["device"] != s.device:
            why = "replay" if isinstance(n, str) and _HEX32.fullmatch(n) else "shape"
        elif pend["mode"] != mode or pend.get("resume") != resume:
            why = "shape"
        elif self.clock() - pend["at"] > NONCE_TTL:
            why = "stale"
        else:
            why = controls.check(self.st, self.h.nonces, self.h.channel, s.device, obj, "term_open", target)
        if not why:
            try:
                f = passkey.approval_fields(obj.get("fa"))
                if f["id"] != pk["id"]:
                    raise passkey.PasskeyError("bad", "credential")
                ch = passkey.elevate_challenge(self.h.channel, s.device, n, "term_open", n,
                                               controls.object_digest("term_open", target))
                self.st.passkey_count(s.device, pk["id"], passkey.verify_assertion(pk, f, ch))
            except (passkey.PasskeyError, ValueError):
                why = "passkey_failed"
        prf = None
        if not why and obj.get("pct") is not None:
            prf = self._open_prf(pend["priv"], obj, s.device, n, n, controls.object_digest("term_open", target))
            why = None if prf is not None else "shape"
        if not why:
            why, extra = self._refusal(s, mode == "enable", resume)
        if why:
            if prf is not None:
                from .elevate import wipe
                wipe(prf)
            self.st.log("control_refused", device=s.device, action="term_open", reason=why)
            self._log("term_open", s.device, "refused:" + why, target)
            return await self._res(s, r, "term_open", False, why=why, **(extra if why == "locked" else {}))
        if size_ok(*size) and "size" in pend:
            pend["size"] = size                   # step 2's size (the window may have changed since step 1)
        self._log("term_open", s.device, "start", target, sig=obj.get("sig"), n=n, ts=obj.get("ts"))
        if resume is not None:
            if prf is not None:
                from .elevate import wipe
                wipe(prf)
            return await self._resume(s, r, pend)
        from .approval_special import phone_origin
        pend['approval_origin'] = phone_origin(self.h, s,
            'terminal_enable' if mode == 'enable' else 'terminal_login', mode)
        self.opening = s.device
        self.h._bg(self._open(s, r, pend, prf, pk), "term_open")
        with contextlib.suppress(Exception):
            await self.h.send_app(s, {"t": "ctl_pending", "r": r, "action": "term_open", "timeout_ms": PENDING_MS})

    def _open_prf(self, priv, obj, device, rid, n, digest) -> bytearray | None:
        """R3: the PRF output as a bytearray (the caller wipes it in a finally)."""
        from .elevate import open_sealed, seal_aad
        try:
            pt = open_sealed(priv, wire.unb64u(obj.get("pepk")), wire.unb64u(obj.get("pct")),
                             seal_aad(self.h.channel, device, rid, "term_prf", n, digest))
        except (ValueError, TypeError):
            return None
        if len(pt) < 16:
            from .elevate import wipe
            wipe(pt)
            return None
        return pt

    def _mode(self, e: dict, prf) -> str:
        if "until" in e:
            return "session"                  # M5: a temporary entry never gets a cached password
        return "prf" if prf else ("local" if local_ok(self.h) else "session")

    async def _open(self, s, r, pend, prf, pk) -> None:
        why, extra, pw = None, {}, None
        try:
            await asyncio.sleep(0)            # ctl_pending first
            enable = pend.get("mode") == "enable"
            # P417 #185: switching on always takes the typed computer password (never a cached one); it becomes an owner entry
            e = {} if enable else store.entry(store.read_config(self.st), s.device) or {}
            if not local_ok(self.h):
                await asyncio.to_thread(store.drop_local, self.st)
            mode = self._mode(e, prf)
            m = store.meta(self.st, s.device) if "until" not in e and not enable else None
            if m:
                got = await asyncio.to_thread(store.load, self.st, self.h.channel, s.device, pk["id"], prf, pend.get("vn"))
                if got is not None:
                    mode = m["mode"]
                    from .elevate import wipe
                    wipe(got)
                else:
                    m = None
            if m is None:
                res = await self._login(s, r, pend, prf, pk, mode, enable)
                pw = res.pop("_pw", None)
                if res.get("result") != "saved":
                    rs = res.get("result")
                    why = {"locked": "locked", "bad_password": "bad_password"}.get(rs, "os" if rs == "failed" else "cancelled")
                    extra = {"secs": res["secs"]} if why == "locked" and res.get("secs") else {}
                elif enable:
                    self._enabled(s.device)
            if not why and (self.h.stopped() or self.term is not None):
                why = "estop" if self.h.stopped() else "busy"
            if not why:
                try:
                    await self._start(s, r, mode, pw, pend.get("size"))
                    pw = None
                except Exception:
                    pw = None                         # the Term owns it now; close() wipes it
                    await self.close("host")
                    raise
        except Exception as ex:  # noqa: BLE001 — never a detail on the wire
            from . import bg
            bg.swallowed(self.st.log, "term_open_failed", ex)
            why = why or "cancelled"
        finally:
            self.opening = None
            if prf is not None:
                from .elevate import wipe
                wipe(prf)                     # R3: the PRF output is a bytearray, zeroed once the open is decided
            if pw is not None:
                from .elevate import wipe
                wipe(pw)
        if why:
            self._log("term_open", s.device, "refused:" + why)
            m = {"t": "ctl_res", "r": r, "action": "term_open", "ok": False, "why": why, **extra}
            if not await self.h.send_app(s, m):
                await self.h._send_device(s.device, m)

    def _enabled(self, device: str) -> None:
        """P417 #185: audit + a notice on every remote + fresh host blocks after a remote switched the terminal on."""
        name = self.h._dname(device) or device
        self.st.log("terminal_cfg", action="enable", via="remote", device=device)
        self._log("terminal_cfg", device, "enable:remote")
        web = store.is_web(self.st, device)   # security review A1: say so when it is the web version (no --web at the computer)
        # RESUME 9 ② (Leo 10-21, ADR-P417 A1 exception): every remote is told which remote switched it on and the one-line way off
        self._notice(f"网页终端已由遥控器「{name}」开启（Face ID + Agent J 主机登录密码）" + ("，它是网页版遥控器" if web else "")
                     + "。不是你本人开启的话，在 Agent J 主机上运行 agentj terminal disable 立即关闭。",
                     f"Web terminal switched on from the remote \"{name}\" (Face ID + the Agent J host login password)"
                     + (", which is the web version" if web else "")
                     + ". If this was not you, run agentj terminal disable on the Agent J host to switch it off at once.")
        self._prefs()

    async def _login(self, s, r, pend, prf, pk, mode, enable: bool = False) -> dict:
        """The `login` card (§11, this device only); its sink checks the password with sudo and stores it per `mode`.
        P417 #185 `enable`: only after sudo accepted the password does this device become an owner entry (term_store)."""
        loop, keep, salt = asyncio.get_running_loop(), {}, pend["salt"]
        sudo, channel, device, st = self.sudo_path(), self.h.channel, s.device, self.st

        login_guard = getattr(self, "login_guard", None)
        def sink(value: bytearray) -> dict:
            from .elevate import run_sudo
            if login_guard is not None and not login_guard():
                return {"result": "cancelled"}
            if not sudo:
                return {"result": "failed", "why": "no_sudo"}
            if store.locked_for(st, device):
                return {"result": "locked", "secs": store.locked_for(st, device)}
            res = run_sudo(["true"], value, "/", 30, sudo)
            # Verification can wait outside the event loop while a scoped
            # assistance grant is revoked/replaced. Recheck BEFORE lock notes,
            # cached-password persistence or a session-memory copy. Owner Desk
            # has no such guard and keeps its existing behavior.
            if login_guard is not None and not login_guard():
                return {"result": "cancelled"}
            if res.get("result") == "bad_password":
                lock = store.note(st, device, False)
                loop.call_soon_threadsafe(self._notice, "网页终端：Agent J 主机登录密码输错了一次" + (f"，已锁定 {lock // 60 + 1} 分钟" if lock else ""),
                                          "Web terminal: a wrong Agent J host login password" + (f", locked for {lock // 60 + 1} min" if lock else ""))
                return {"result": "locked", "secs": lock} if lock else {"result": "retry"}
            if res.get("result") != "done" or res.get("prompts") != 1:
                return {"result": "failed", "why": "verify"}     # NOPASSWD / no sudo / timeout: not a checked password
            store.note(st, device, True)
            if enable:
                store.add_owner_entry(st, device, store.is_web(st, device))
            if login_guard is not None and not login_guard():
                return {"result": "cancelled"}
            if mode in ("prf", "local"):
                store.save(st, channel, device, pk["id"], mode, salt, value, prf if mode == "prf" else None)
            else:
                keep["pw"] = bytearray(value)
            return {"result": "saved", "receipt": {"name": "login", "dest": "terminal", "length": 0, "verify": "ok"}}
        card = {"kind": "login", "term": r, "only": device, "sink": sink, "cwd": "/"}
        from .approval_review import origin_scope
        with origin_scope(pend.get('approval_origin') or {}), self.h_paired(s):
            res = await self.h.elevate.request(card)
        if res.get("result") == "saved" and "pw" in keep:
            res["_pw"] = keep.pop("pw")
        for v in keep.values():
            from .elevate import wipe
            wipe(v)
        return res

    async def _start(self, s, r, mode, pw, size=None) -> None:
        cfg = store.read_config(self.st)
        x = Term(s, r, mode, pw, cfg["minutes"], self.clock, self.wall)
        if size is not None and self.pty_supported():          # 0.26.9 (ADR-0269-pty)
            x.kind, (x.cols, x.rows), x.vmask = "pty", size, VtMasker()
        self.term = x
        self.tr_start(x, store.entry(cfg, s.device) or {})        # P414: the transcript (none for a friend's session, L3)
        self.owner_acted()
        d = store.root(self.st)
        from . import main_identity, service
        cwd = str(main_identity.working_root(self.h.agent_cfg or {}))
        cwd = cwd if os.path.isdir(cwd) else os.path.expanduser("~")
        sock = d / "askpass.sock"
        with contextlib.suppress(FileNotFoundError):
            sock.unlink()
        old = os.umask(0o077)
        try:
            x.server = await asyncio.start_unix_server(lambda rd, wr: self._askpass(x, rd, wr), path=str(sock))
        finally:
            os.umask(old)
        os.chmod(sock, 0o600)
        if self.python is None:
            self.python = await asyncio.to_thread(safe_python) or ""
        x.helper = Helper(d, sock, self.python) if self.python else None     # R3 (H-1): a system interpreter or no askpass
        hist = d / "history"
        os.close(os.open(hist, os.O_WRONLY | os.O_CREAT, 0o600))
        os.chmod(hist, 0o600)
        shell = self.shell or (term_mac.pick_shell() if sys.platform == "darwin" else "bash")
        x.shell = shell
        env = shell_env(dict(os.environ), await asyncio.to_thread(service_keys))
        from .term_direct import pristine_env
        # PTY/login shells may load arbitrary configuration. Any later non-profile
        # input permanently invalidates this session's proof, including owner input.
        x.h1_pristine = x.kind == "line" and shell == "bash" and os.path.exists("/bin/bash") and pristine_env(env)
        if sys.platform == "darwin":
            env = term_mac.fix_env(env)                  # P437: launchd's bare PATH and C locale
        env.update(INPUTRC="/dev/null", TERM="dumb", **{TOKEN_ENV: x.token})
        from . import term_pty as _tp                # w2-sec N-3: the session's own cgroup where serve may make one (Linux)
        env["PATH"] = _tp.system_first(env.get("PATH"))      # w2-sec R3-4: system dirs before user-writable ones
        x.cg = await asyncio.to_thread(_tp.cg_make, secrets.token_hex(8))
        if x.cg:
            env["AJ_TERM_CG"] = x.cg
        if x.kind == "pty":
            shell = pty_shell(self.pty_shell)
            x.shell = shell
        # #184: the shell reads this file at start-up, not ~/.bash_history / ~/.zsh_history, and the space-led setup lines
        # are not recorded
        env.update(HISTFILE=str(hist), HISTSIZE="2000", HISTFILESIZE="2000", HISTCONTROL="ignorespace")
        if x.helper is not None:
            env["SUDO_ASKPASS"] = str(x.helper.path)
        sudo = self.sudo_path()
        mark = x.prompt[len("\x1b]133;A;"):-1]
        await asyncio.to_thread(self._scrub, hist, shell)   # #184: a file an older version wrote is cleaned too
        nopy = term_mac.NO_PYTHON if sys.platform == "darwin" else NO_PYTHON
        if x.kind == "pty":
            from .term_shell import HIST_IGNORE
            await self._pty_spawn(x, cwd, env, str(hist), sudo, x.helper is not None, mark, HIST_IGNORE, nopy, shell)
            argv = setup = None
        elif shell == "zsh":
            from .term_shell import HIST_IGNORE
            argv = list(term_mac.ZSH_ARGV)
            setup = term_mac.zsh_setup(str(hist), sudo, x.helper is not None, mark, HIST_IGNORE, nopy)
        else:
            argv = ["/bin/bash" if os.path.exists("/bin/bash") else (shutil.which("bash") or "bash"), "--noprofile", "--norc",
                    "--noediting", "-i"]
            setup = [" set +o history", "set +H +m",
                     f"HISTFILE={shlex.quote(str(hist))}; HISTSIZE=2000; HISTFILESIZE=2000; {HIST_SETUP}"]
            if sudo and x.helper is not None:   # R3 (H-2): -k on every call — sudo's own timestamp never skips the phone's card
                setup += [f"sudo() {{ {shlex.quote(sudo)} -k -A \"$@\"; }}", f"{shlex.quote(sudo)} -k 2>/dev/null"]
            elif sudo:
                setup += [f"sudo() {{ printf '%s\\n' {shlex.quote(nopy)} >&2; return 1; }}", f"{shlex.quote(sudo)} -k 2>/dev/null"]
            # P414 (R3-1): history on in the SAME line as the ready marker — exactly one prompt follows the marker, so a line
            # sent right after it never takes a stale setup prompt as its own (an empty 「!」 result); zsh_setup ends the same way
            setup += [f"PS1=$'\\\\[\\e]133;A;{mark}\\a\\\\]\\\\w \\\\$ '; PS2='> '",
                      f"set -o history; printf '\\033]133;R;{mark}\\007'"]
        if x.kind == "line":
            ours, theirs = socket.socketpair()   # R3 (M-1): a socket, not a pipe — /proc/<pid>/fd/0 cannot be reopened (ENXIO)
            if x.cg:                                 # w2-sec N-3: into the session's cgroup before bash runs anything
                argv = [sys.executable, "-I", "-S", "-c", _tp.CG_SHIM, *argv]
            try:
                x.proc = await asyncio.create_subprocess_exec(*argv, stdin=theirs.fileno(), stdout=theirs.fileno(),
                                                              stderr=theirs.fileno(), cwd=cwd, env=env, start_new_session=True)
            finally:
                theirs.close()
            x.reader, x.writer = await asyncio.open_unix_connection(sock=ours)
            x.writer.write(("\n".join(setup) + "\n").encode())
            await x.writer.drain()
        x.cast = await asyncio.to_thread(self._cast_open, x)
        x.tasks.append(self.h._bg(self._pty_pump(x) if x.kind == "pty" else self._pump(x), "term_pump"))
        if self.watch is None or self.watch.done():
            self.watch = self.h._bg(self._watch(), "term_watch")
        self.st.log("term_open", device=x.device, result="ready", status=mode, kind=x.kind)
        self._notice(f"网页终端已打开（{self.h._dname(x.device) or x.device}，最长 {cfg['minutes']} 分钟）",
                     f"Web terminal opened ({self.h._dname(x.device) or x.device}, up to {cfg['minutes']} min)")
        m = {"t": "term_ready", "r": r, "id": x.id, "expires_at": x.expires_at, "idle_s": store.IDLE_S, "sudo": mode,
             "cwd": service.tilde(cwd), "mode": x.kind}
        x.ready_msg = dict(m)
        await self.h.send_app(s, m)
        self._prefs()

    # ------------------------------------------------------------ lines
    async def on_prefill(self, x: Term, s, obj: dict) -> None:
        """A chat command is data, never a program's answer or an implicit Enter."""
        cmd, seq = obj.get("cmd"), obj.get("seq")
        now, cfg = self.clock(), store.prune(self.st)
        e = store.entry(cfg, s.device)
        why = None
        if type(seq) is not int or not isinstance(cmd, str) or not cmd or len(cmd) > LINE_MAX or \
                any(ord(c) < 32 or 127 <= ord(c) <= 159 or c in "\u2028\u2029" for c in cmd) or line_problem(cmd):
            why = "shape"
        elif s.device != x.device or not self.st.is_allowed(s.pub):
            why = "not_writer"
        elif self.h.stopped():
            why = "estop"
        elif not cfg["on"] or e is None or e.get("until", self.wall() + 1) <= self.wall() or \
                not self.st.passkey_of_device(s.device) or (store.is_web(self.st, s.device) and not e["web"]):
            why = "disabled"
        elif now >= x.expires:
            why = "timeout"
        elif now - x.last >= store.IDLE_S:
            why = "idle"
        elif x.kind != "pty" or not x.at_prompt or x.hold is not None or x.io is None or \
                not self.shell_front(x, strict=True) or password_mode(x.io.fd) or \
                x.edit or not x.edit_ok:
            why = "busy"
        elif not self._pty_write(x, cmd):
            why = "full"
        else:
            from .term_pty import edit_apply
            edit_apply(x, cmd)
            x.last = self.clock()
            self.owner_acted()
        self._log("term_prefill", s.device, "refused:" + why if why else "ok", {"id": x.id})
        await self.h.send_app(s, {"t": "term_prefill_res", "id": x.id, "seq": seq, "ok": why is None,
                                  **({"why": why} if why else {})})

    async def _resume(self, s, r, pend) -> None:
        """Fresh UV bound to the old id; same paired owner, original expiry, no new writer."""
        x = self.term
        async with x.frame_lock:
            why, extra = self._refusal(s, False, pend["resume"])
            if why:
                return await self._res(s, r, "term_open", False, why=why, **extra)
            if x.hold is not None:
                self._pty_cancel(x, "reconnect")
            for ask in x.sudo.values():
                if not ask["fut"].done():
                    ask["fut"].set_result(None)
            x.s = s
            x.disconnected_at, x.hidden = None, None
            await self.h.send_app(s, {**x.ready_msg, "r": r, "resumed": 1})
            for data, prompt in x.replay:
                x.seq += 1
                await self.h.send_app(s, {"t": "term_out", "id": x.id, "seq": x.seq, "d": data,
                                          **({"prompt": True} if prompt else {})})
            x.viewers = [s]
            if "size" in pend:
                await self.on_resize(x, s, {"cols": pend["size"][0], "rows": pend["size"][1]})
            self._log("term_resume", x.device, "ready", {"id": x.id})
        self._prefs()

    async def on_line(self, x: Term, s, obj: dict) -> None:
        line, seq = obj.get("line"), obj.get("seq")
        # 0.26.9: the signed answer to a held PTY Enter carries the shell's own line (a bracketed paste may hold \n / TAB);
        # it must equal the held copy exactly (checked below), so only its size is limited here
        held = x.kind == "pty" and x.hold == "confirm" and isinstance(obj.get("n"), str) and obj.get("n") == x.hold_n
        if held and isinstance(line, str) and type(seq) is int and len(line.encode("utf-8", "replace")) <= 8 * LINE_MAX:
            pass
        elif not isinstance(line, str) or type(seq) is not int or len(line.encode("utf-8", "replace")) > LINE_MAX or \
                any(ord(c) < 32 or ord(c) == 127 for c in line) or wire.text_problem(line, LINE_MAX):
            self.st.log("control_refused", device=s.device, action="term_line", reason="shape")
            if held:
                self._pty_cancel(x, "shape")
            return
        x.last = self.clock()
        self.owner_acted()                                   # P414 (L4): an owner action resets the term_go budget
        bang = obj.get("bang")
        if bang is not None and not wire.is_id22(bang):
            self.st.log("control_refused", device=s.device, action="term_line", reason="shape")
            return
        self.run_mark(x, bang, obj)                          # #584: a reply code block's 「执行」 (term_bang.py)
        if line_problem(line) and not held:                  # P414 (H1c): invisible / direction characters, > 8 spaces
            self.st.log("control_refused", device=s.device, action="term_line", reason="invisible")
            return await self._frame(x, "\n[这一行含有看不见的字符或一长串空格，没有运行 / the line has invisible characters or a long run "
                                        "of spaces: not run]\n", False)
        if bang is not None and getattr(x, "bang", None) is not None:
            return await self.bang_refuse(x, bang)          # P414 (§37.12): one running 「!」 at a time
        if x.kind == "pty" and "n" not in obj:               # 0.26.9: a whole line into the PTY only at the shell prompt
            if not x.at_prompt and not x.running and x.hold is None:
                x.early = (s, obj)                           # before the first prompt: sent when it comes
                return
            if not x.at_prompt or x.hold is not None:
                self.st.log("control_refused", device=s.device, action="term_line", reason="program")
                return await self.bang_refuse(x, bang, "program") if bang is not None else None
        if bang is not None and not x.at_prompt and x.running:   # (before the first prompt nothing runs yet: not refused)
            # P414 (R3-1): a program is reading input (a password, a passphrase…) — the line must reach neither the Agent nor
            # the owner page as `$ line`, and is not written: the owner answers the program in the full-screen terminal.
            self.st.log("control_refused", device=s.device, action="term_line", reason="program")
            return await self.bang_refuse(x, bang, "program")
        if "n" in obj:                                       # the signed + Face ID resend of a confirmed line
            n = obj.get("n")
            c = x.confirms.pop(n, None) if isinstance(n, str) else None
            why = None
            if c is None or c["line"] != line:
                why = "replay"
            elif self.clock() > c["exp"]:
                why = "stale"
            else:
                why = controls.check(self.st, self.h.nonces, self.h.channel, s.device, obj, "term_line", {"id": x.id, "line": line})
            if not why and c.get("uv", True):          # P573: Face ID only for a line of the three classes
                why = self._assert(s, obj, x.id, "term_line", n, _sha(line))
            self._log("term_line", s.device, "refused:" + why if why else "confirmed", {"id": x.id, "line": line},
                      sig=obj.get("sig"), n=n, ts=obj.get("ts"))
            held = x.kind == "pty" and x.hold == "confirm" and n == x.hold_n
            if why:
                if held:
                    self._pty_cancel(x, why)                 # 0.26.9: a failed answer erases the held line
                return
            if held and c.get("pty") == "enter":
                return self._pty_release(x, line)            # 0.26.9: the held Enter goes, nothing is retyped
            return await self._write(x, line, bang)
        bad = classify(line)
        # P414 term_go still always confirms. H1's exception is a closed literal
        # output profile in a proved pristine shell; all other run lines confirm.
        direct = getattr(x, "h1_pristine", False) and _readonly(line)
        agent = self.go_match(line) or (obj.get("run") == 1 and not bad and not direct)
        if agent:
            bad = ("Agent 填写的命令", "filled in by the Agent")
        if bad:
            n = secrets.token_hex(16)
            x.confirms = {k: v for k, v in x.confirms.items() if v["exp"] > self.clock()}
            uv = line_needs_face(line)
            x.confirms[n] = {"line": line, "exp": self.clock() + CONFIRM_TTL, "uv": uv}
            ch = passkey.elevate_challenge(self.h.channel, s.device, x.id, "term_line", n, _sha(line))
            self._log("term_line", s.device, "confirm:agent" if agent else "confirm", {"id": x.id, "line": line})
            await self.h.send_app(s, {"t": "term_confirm", "id": x.id, "seq": seq, "line": line, "why": bad[0], "why_en": bad[1],
                                      "n": n, "fa_ch": wire.b64u(ch), "uv": 1 if uv else 0, **({"agent": 1} if agent else {})})
            return
        await self._write(x, line, bang)

    def _assert(self, s, obj, rid, kind, n, digest) -> str | None:
        """The passkey assertion of a confirmed line (or another Face ID step of the terminal)."""
        pk = self.st.passkey_of_device(s.device)
        if not pk:
            return "no_passkey"
        try:
            f = passkey.approval_fields(obj.get("fa"))
            if f["id"] != pk["id"]:
                raise passkey.PasskeyError("bad", "credential")
            self.st.passkey_count(s.device, pk["id"], passkey.verify_assertion(
                pk, f, passkey.elevate_challenge(self.h.channel, s.device, rid, kind, n, digest)))
        except (passkey.PasskeyError, ValueError):
            return "passkey_failed"
        return None

    async def _write(self, x: Term, line: str, bang: str | None = None) -> None:
        if not _readonly(line):
            x.h1_pristine = False  # before any await/write: aliases/functions/options may now change
        if x.kind == "pty":
            return None if x.proc is None or x.closing else await self.pty_line(x, line, bang)
        if x.proc is None or x.writer is None or x.closing:
            return
        early = not x.at_prompt and not x.running            # P414 (R3-1): before the first prompt — a shell line, not N5 input
        if early:
            x.skip_prompt = 1                                # the setup's own prompt comes next: it is not this line's end
        rec = line if x.at_prompt or early else UNRECORDED   # N5: input into a running program is not recorded
        if x.at_prompt or early:
            x.line = line
        m = Masker()
        shown = (m.feed(rec + "\n") + m.close()) if rec != UNRECORDED else rec + "\n"
        # P414 (R2-N2): the transcript gets the line through the SAME owner masking as the cast (Masker → privacy.owner_text),
        # never raw — a token typed in a line (`echo <tok>`, `curl -H "X-Api: <tok>"`) must not come back via terminal_read.
        self.tr_add(x, ("$ " + shown) if rec != UNRECORDED else shown)
        if bang is not None:
            self.bang_start(x, bang, line)                   # P414 (§37.12): this line's output goes back as a chat turn
        x.at_prompt, x.running = False, True
        await self._cast(x, "i", shown)
        with contextlib.suppress(Exception):     # never await the pipe here: this runs in the relay receive loop
            if x.writer.transport.get_write_buffer_size() > 64 * 1024:
                return await self._frame(x, "\n[Agent J 主机上的程序没有在读输入，这一行没有送出 / the program is not reading input]\n", False)
            x.writer.write((line + "\n").encode())

    def _signal_children(self, x: Term, sig) -> None:
        if x.proc is None:
            return
        for pid in in_session(x.proc.pid, "", self.proc):
            if pid != x.proc.pid:
                with contextlib.suppress(OSError):
                    os.kill(pid, sig)

    # ------------------------------------------------------------ output
    async def _pump(self, x: Term) -> None:
        dec, tail, ready = codecs.getincrementaldecoder("utf-8")("replace"), "", False
        why = "owner"
        try:
            while not x.closing:
                try:
                    raw = await asyncio.wait_for(x.reader.read(4096), 0.3)
                except asyncio.TimeoutError:
                    if ready:
                        await self._out(x, x.masker.flush(), False)
                    continue
                if not raw:
                    break
                text = tail + dec.decode(raw)
                i = text.rfind("\x1b", max(0, len(text) - 40))
                tail = ""
                if i >= 0 and (x.prompt.startswith(text[i:]) or x.ready.startswith(text[i:])) and text[i:] not in (x.prompt, x.ready):
                    text, tail = text[:i], text[i:]
                if not ready:
                    k = text.find(x.ready)
                    if k < 0:
                        continue                              # bash's own start-up lines and the setup's prompts
                    ready, text = True, text[k + len(x.ready):]
                text = text.replace(x.ready, "")
                x.recent = (x.recent + text)[-2048:]
                parts = text.split(x.prompt)
                for j, part in enumerate(parts):
                    prompt = j < len(parts) - 1
                    if prompt and getattr(x, "skip_prompt", 0):
                        x.skip_prompt, prompt = x.skip_prompt - 1, False   # P414 (R3-1): the setup's prompt, see _write
                    if prompt:
                        x.at_prompt, x.running = True, False
                    await self._out(x, x.masker.feed(part), prompt)
        except Exception as ex:  # noqa: BLE001
            from . import bg
            bg.swallowed(self.st.log, "term_pump_failed", ex)
            why = "host"
        if not x.closing:
            await self.close(why)

    async def _out(self, x: Term, data: str, prompt: bool) -> None:
        if not data and not prompt:
            return
        now = self.clock()
        if now - x.win >= 1.0:
            x.win, x.win_bytes = now, 0
            if x.dropped:
                note, x.dropped = f"\n[输出太多，已省略 {x.dropped} 字节]\n", 0
                await self._frame(x, note, False)
        size = len(data.encode("utf-8", "replace"))
        if x.win_bytes + size > RATE:
            x.dropped += size
            data = ""
            if not prompt:
                return
        x.win_bytes += size
        self.tr_add(x, data)                                 # P414: transcript + the running 「!」 get what the page gets
        self.bang_out(x, data, prompt)
        await self._cast(x, "o", data)
        for k in range(0, max(1, len(data)), FRAME_CHARS):
            last = k + FRAME_CHARS >= len(data)
            await self._frame(x, data[k:k + FRAME_CHARS], prompt and last)

    async def _frame(self, x: Term, data: str, prompt: bool) -> None:
        async with x.frame_lock:
            if x.kind == "pty":
                x.replay.append((data, prompt))
                x.replay_chars += len(data)
                while x.replay_chars > REPLAY_CHARS and len(x.replay) > 1:
                    x.replay_chars -= len(x.replay.pop(0)[0])
            x.seq += 1
            m = {"t": "term_out", "id": x.id, "seq": x.seq, ("d" if x.kind == "pty" else "data"): data}
            if prompt:
                m["prompt"] = True
            for v in list(x.viewers):
                with contextlib.suppress(Exception):
                    await self.h.send_app(v, m)

    # ------------------------------------------------------------ the recording (asciicast v2, M4)
    def _cast_open(self, x: Term):
        d = store.root(self.st) / "casts"
        d.mkdir(mode=0o700, exist_ok=True)
        cut = self.wall() - CAST_DAYS * 86400
        for p in d.glob("*.cast"):
            with contextlib.suppress(OSError):
                if p.stat().st_mtime < cut:
                    p.unlink()
        p = d / f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(self.wall()))}-{x.device}.cast"
        x.cast_name = p.name
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        pty = x.kind == "pty"
        os.write(fd, (json.dumps({"version": 2, "width": x.cols if pty else 80, "height": x.rows if pty else 24,
                                  "timestamp": int(self.wall()), "env": {"TERM": "xterm-256color" if pty else "dumb"},
                                  "title": "agentj web terminal"}) + "\n").encode())
        return fd

    async def _cast(self, x: Term, kind: str, data: str) -> None:
        if x.cast is None or not data:
            return
        ln = (json.dumps([round(self.clock() - x.t0, 3), kind, data], ensure_ascii=False) + "\n").encode()
        with contextlib.suppress(OSError):
            await asyncio.to_thread(os.write, x.cast, ln)

    # ------------------------------------------------------------ forget
    async def on_forget(self, s, obj: dict) -> None:
        r = obj.get("r")
        if not wire.is_rid(r):
            return
        why = controls.check(self.st, self.h.nonces, self.h.channel, s.device, obj, "term_forget", {})
        self._log("term_forget", s.device, "refused:" + why if why else "ok", {}, sig=obj.get("sig"), n=obj.get("n"),
                  ts=obj.get("ts"))
        if why:
            return await self._res(s, r, "term_forget", False, why=why)
        await asyncio.to_thread(store.forget, self.st, s.device)
        x = self.term
        if x is not None and x.device == s.device and x.pw:
            from .elevate import wipe
            wipe(x.pw)
            x.pw = None
        await self._res(s, r, "term_forget", True)
        self._prefs()

    # ------------------------------------------------------------ limits and close (M2)
    async def tick(self) -> None:
        """One check of every limit (the watchdog calls it each second; tests call it with a fake clock)."""
        x = self.term
        if x is None or x.closing:
            return
        now, s = self.clock(), x.s
        cfg = store.read_config(self.st)
        e = store.entry(cfg, x.device)
        if self.h.stopped():
            return await self.close("estop")
        if not cfg["on"] or e is None or ("until" in e and e["until"] <= self.wall()):
            return await self.close("disabled")
        if now >= x.expires:
            return await self.close("timeout")
        if now - x.last >= store.IDLE_S:
            return await self.close("idle")
        if self.h.sessions.get(s.cid) is not s or s.state != "ready":
            self.session_gone(s)
            if x.kind != "pty" or now - x.disconnected_at >= RECONNECT_S:
                return await self.close("relay_lost")
            return
        self.bang_tick(x)
        if x.kind == "pty" and self.pty_blocked():        # w2-sec H-3: a fenced Agent started without a private /dev
            return await self.close("disabled")
        if x.kind == "pty":
            self.pty_tick(x)                                 # 0.26.9: an unanswered confirm erases the held line after 120 s
        x.hidden = None if s.fg else (x.hidden if x.hidden is not None else now)
        if x.hidden is not None and now - x.hidden >= HIDDEN_S:
            return await self.close("background")

    async def _watch(self) -> None:
        while self.term is not None:
            await asyncio.sleep(1)
            with contextlib.suppress(Exception):
                await self.tick()

    def session_gone(self, s) -> None:
        """PTY: bounded reconnect grace; line mode retains its immediate-close contract."""
        x = self.term
        if x is not None and x.s is s and not x.closing:
            if x.kind == "pty":
                if x.disconnected_at is None:
                    if x.hold is not None:
                        self._pty_cancel(x, "disconnect")
                    x.disconnected_at = self.clock()
                    x.viewers = []
                    for ask in x.sudo.values():
                        if not ask["fut"].done():
                            ask["fut"].set_result(None)
                    self._log("term_disconnect", x.device, "waiting", {"id": x.id})
            else:
                self.h._bg(self.close("relay_lost"), "term_close")

    async def close(self, why: str) -> None:
        x = self.term
        if x is None or x.closing:
            return
        x.closing = True
        if x.server is not None:
            x.server.close()
        for w in x.sudo.values():
            if not w["fut"].done():
                w["fut"].set_result(None)
        if x.helper is not None:
            x.helper.remove()
        with contextlib.suppress(FileNotFoundError):
            (store.root(self.st) / "askpass.sock").unlink()
        if x.proc is not None:
            await self._kill(x)
            with contextlib.suppress(OSError):           # #184: after the shell wrote it on SIGHUP
                await asyncio.to_thread(self._scrub, store.root(self.st) / "history", x.shell)
        if x.writer is not None:
            with contextlib.suppress(Exception):
                x.writer.close()
        self.pty_closed(x)                                   # 0.26.9: the master, the tty registry, the start-up files
        tail = (x.vmask or x.masker).close()
        if tail:
            self.tr_add(x, tail)
            self.bang_out(x, tail, False)
            await self._frame(x, tail, False)
            await self._cast(x, "o", tail)
        self.tr_close(x)                                     # P414: a running 「!」 ends `closed`; the transcript stays ≤ 30 min
        if x.pw:
            from .elevate import wipe
            wipe(x.pw)
            x.pw = None
        cast = x.cast_name
        if x.cast is not None:
            with contextlib.suppress(OSError):
                os.close(x.cast)
            x.cast = None
        for t in x.tasks:
            if t is not asyncio.current_task():
                t.cancel()
        minutes = max(1, round((self.clock() - x.t0) / 60))
        self.term = None
        self.st.log("term_close", device=x.device, why=why, ms=int((self.clock() - x.t0) * 1000))
        self._log("term_close", x.device, f"{why}:{minutes}m" + (f":{cast}" if cast else ""))
        with contextlib.suppress(Exception):
            await self.h.send_app(x.s, {"t": "term_close", "id": x.id, "why": why, "minutes": minutes})
        zh = {"owner": "主动结束", "timeout": "到时间了", "idle": "太久没输入", "background": "手机切到后台", "relay_lost": "连接断了",
              "estop": "全部停下", "host": "Agent J 主机端结束", "disabled": "已在 Agent J 主机上关闭"}.get(why, why)
        self._notice(f"网页终端已关闭（{zh}，{minutes} 分钟）", f"Web terminal closed ({why}, {minutes} min)")
        self._prefs()

    async def _kill(self, x: Term) -> None:
        pid = x.proc.pid
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(pid, signal.SIGHUP)
        try:
            await asyncio.wait_for(x.proc.wait(), KILL_GRACE)
        except asyncio.TimeoutError:
            pass
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(pid, signal.SIGKILL)
        for p in await asyncio.to_thread(in_session, pid, x.token, self.proc):
            with contextlib.suppress(OSError):
                os.kill(p, signal.SIGKILL)
        from .term_pty import cg_kill                # w2-sec N-3: also what left the session AND dropped the token;
        await asyncio.to_thread(cg_kill, getattr(x, "cg", None))   # R3-2: cgroup.kill when the kernel has it
        with contextlib.suppress(Exception):
            await asyncio.wait_for(x.proc.wait(), 2)

    def device_gone(self, did: str) -> None:
        """Unpaired / replaced: its entry and its cache go; its terminal closes."""
        with contextlib.suppress(Exception):
            store.prune(self.st)
            store.forget(self.st, did)
        if self.term is not None and self.term.device == did:
            self.h._bg(self.close("disabled"), "term_close")

    def config_applied(self) -> None:
        """apply_preferences: a `local` cache dies the moment isolation is off or the session mode is shared (N2)."""
        if not local_ok(self.h):
            with contextlib.suppress(Exception):
                store.drop_local(self.st)
