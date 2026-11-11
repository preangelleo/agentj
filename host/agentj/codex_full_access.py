"""0.30.7 (chairman 11-10): 「改回全放行并重启」 / "Back to full access & restart" — one host-defined owner button.

When Codex's own sandbox refuses a step because the owner wrote a top-level `sandbox_mode` in config.toml (agent_codex
CONFIG_NOTICE, perm_source "config"), the refusal page carries this button (typed host metadata `card.fix`, never inferred
from text). The same NAME is a row of the host command list (cmdrun.RUN_CMDS, Settings 「Agent J 主机上的命令」).

- The phone sends only the NAME as the signed control `cmd_run` (control-v1, object {"cmd": NAME}) from a paired device;
  nonce one-shot (controls.check), replay / forged / unpaired refused before anything runs.
- It widens the Agent's power (back to danger-full-access): classified like `approval_widen` (confirm_tier, irreversible
  tier) → the same tap carries a Face ID / passkey assertion over that object; no passkey → `no_passkey`.
- What runs is fixed here, in process: codex_perm.set_mode(None) (top level removed, misplaced copies removed, backup
  kept) and then the installed service's restart (service.restart) — never a shell, never an argument from the phone.
- Before the restart: one controls.log line and one approvals.log line (device, before / after mode, result) and a pending
  receipt (0600) in the state dir. After serve comes back the first ready phone session gets the receipt as a system page
  (「已回到全放行」 / "Back to full access"), checked against config.toml again; the delivery is audited too.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import time

NAME = "codex_full_access"
RECEIPT = "codex-full-access-receipt.json"
UV_ACTION = "approval_widen"           # confirm_tier row this widening shares (approval level / goal / Jev off)


def requires_uv() -> bool:
    from . import confirm_tier
    return confirm_tier.owner_action(UV_ACTION) == confirm_tier.UV


def offered(perm_source) -> bool:
    """The refusal page carries the button only for the owner's own top-level config.toml setting (not a desktop thread,
    not research, not Agent J's default) — and only while the file really holds one."""
    if perm_source != "config":
        return False
    from . import codex_perm
    try:
        info = codex_perm.config_info()
    except Exception:  # noqa: BLE001 — unknown → no button (the text still names the terminal way)
        return False
    if info.get("error") or info.get("top") not in codex_perm.MODES:
        return False
    # r1 A-1 / A-2: no button the tap could not honour — a symlinked config.toml (the owner's dotfiles; never edited
    # through) or a selected [profiles.<p>] that keeps its own sandbox_mode after the top-level key is gone
    return codex_perm.is_regular_or_absent() and codex_perm.profile_restricts() is None


def _mode(info) -> str:
    from . import codex_perm
    top = info.get("top") if isinstance(info, dict) else None
    return top if top in codex_perm.MODES else "default"


def _audit(st, device, result, before, after, extra=None, *, sig=None):
    """controls.log line for each step (start / outcome / receipt). The approvals.log line is `_pre_record`."""
    from . import controls
    rec = {"before": before, "after": after, **(extra or {})}
    controls.log(st, action="cmd_run", device=device, result=result, obj={"cmd": NAME},
                 **({"sig": sig.get("sig"), "n": sig.get("n"), "ts": sig.get("ts")} if sig else {}),
                 extra={"cmd": NAME, **{k: v for k, v in rec.items() if isinstance(v, (str, int, bool))}})


def _pre_record(st, device, sig, before) -> bool:
    """r1 A-3: the owner's signed decision goes to approvals.log BEFORE config.toml is touched ("every phone approval is
    logged"). Recorded as the signed control it is (tool `cmd_run`, object {"cmd": NAME}) so `approvals.check_record`
    verifies it against the device key. A failure is written to host.log and the action does not run."""
    from . import approvals, controls, wire
    digest = controls.object_digest("cmd_run", {"cmd": NAME})
    try:
        approvals.record(st, rid=sig["rid"], agent="phone", tool="cmd_run",
                         input_sha256=hashlib.sha256(f"{NAME}:{before}->default".encode()).hexdigest(), shown_sha256=digest,
                         decision="allow", reason="device", device=device, sign_pub=st.sign_key(device),
                         sig=wire.unb64u(sig["sig"]), control={"n": sig["n"], "sig_ts": sig["ts"], "object_sha256": digest})
        return True
    except Exception as e:  # noqa: BLE001 — logged (type only), and nothing is changed
        with contextlib.suppress(Exception):
            st.log("cmd_run_audit_failed", device=device, cmd=NAME, err=type(e).__name__[:40])
        return False


def apply(st, device: str, sig: dict, *, status=None) -> dict:
    """The fixed action (runs in a worker thread). → {"code", "out", "before", "after", "restart"}. Never raises."""
    from . import codex_perm, service
    status = status or service.status
    before_info = codex_perm.config_info()
    before = _mode(before_info)
    if not _pre_record(st, device, sig, before):       # r1 A-3: first, before any branch; no line → nothing runs
        _audit(st, device, "refused:audit_failed", before, before, sig=sig)
        return {"code": 1, "before": before, "after": before, "restart": "none", "why": "audit_failed"}
    if before_info.get("error"):
        _audit(st, device, "refused:config_invalid", before, before, sig=sig)
        return {"code": 2, "before": before, "after": before, "restart": "none", "why": "config_invalid"}
    if not codex_perm.is_regular_or_absent():          # r1 A-1: a symlinked / special config.toml is the owner's own
        _audit(st, device, "refused:config_invalid", before, before, {"detail": "not_regular"}, sig=sig)
        return {"code": 2, "before": before, "after": before, "restart": "none", "why": "config_invalid",
                "detail": "not_regular"}
    prof = codex_perm.profile_restricts()
    if prof:                                           # r1 A-2: removing the top-level key would not give full access
        _audit(st, device, "refused:profile_restricts", before, before, sig=sig)
        return {"code": 2, "before": before, "after": before, "restart": "none", "why": "profile_restricts",
                "profile": prof}
    if before == "default" and not before_info.get("misplaced"):
        _audit(st, device, "ok:unchanged", before, before, sig=sig)
        return {"code": 0, "before": before, "after": before, "restart": "none", "why": "unchanged"}
    try:
        r = codex_perm.set_mode(None)
    except codex_perm.NotRegular:
        _audit(st, device, "refused:config_invalid", before, before, {"detail": "not_regular"}, sig=sig)
        return {"code": 2, "before": before, "after": before, "restart": "none", "why": "config_invalid",
                "detail": "not_regular"}
    except (OSError, ValueError) as e:
        _audit(st, device, "failed:" + type(e).__name__[:40], before, before, sig=sig)
        return {"code": 1, "before": before, "after": before, "restart": "none", "why": "write_failed"}
    after = _mode(r)
    receipt = {"at": int(time.time()), "device": device, "before": before, "after": after, "rid": sig["rid"],
               "pid": os.getpid()}
    try:
        svc = status()
        live = bool(svc.get("installed")) and svc.get("active") == "active"
    except Exception:  # noqa: BLE001 — no service manager: never a traceback, the owner restarts by hand
        live = False
    receipt["restart"] = "requested" if live else "manual"
    st.write_private(st.root / RECEIPT, json.dumps(receipt).encode())      # first: it must outlive the restart
    _audit(st, device, "ok", before, after, {"restart": receipt["restart"]}, sig=sig)
    return {"code": 0, "before": before, "after": after, "restart": receipt["restart"]}


def restart_now(st, device: str, res: dict, *, restart=None) -> None:
    """After the phone got its answer: restart the installed service (worker thread). A failure is kept in the receipt
    (delivered at once by the caller) and audited; never raised."""
    from . import service
    if res.get("restart") != "requested":
        return
    try:
        (restart or service.restart)()
    except Exception as e:  # noqa: BLE001 — reported in the receipt, never raised
        res["restart"] = "failed"
        with contextlib.suppress(Exception):
            d = json.loads((st.root / RECEIPT).read_text())
            d["restart"] = "failed"
            st.write_private(st.root / RECEIPT, json.dumps(d).encode())
        _audit(st, device, "restart_failed:" + type(e).__name__[:40], res.get("before"), res.get("after"))


def take_receipt(st) -> dict | None:
    """The pending receipt, removed (delivered once)."""
    p = st.root / RECEIPT
    try:
        d = json.loads(p.read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError, UnicodeDecodeError):
        d = None
    with contextlib.suppress(OSError):
        p.unlink()
    return d if isinstance(d, dict) else {"broken": True}


def receipt_text(rec: dict, lang: str) -> tuple[bool, str]:
    """(back to full access?, the one-language system page text) — checked against config.toml as it is now."""
    from . import codex_perm
    info = codex_perm.config_info()
    top_ok = not info.get("error") and info.get("top") is None and not info.get("misplaced")
    zh = lang != "en"
    prof = codex_perm.profile_restricts() if top_ok else None
    if prof:                                            # r1 A-2: the selected profile still decides — say so plainly
        return False, profile_text(prof, zh)
    ok = top_ok
    if ok and rec.get("restart") in ("requested", None):
        return True, ("已回到全放行：Codex 不再用 config.toml 里的沙箱（danger-full-access），Agent J 已重启，同一段对话从下一条消息起按新设置。"
                      if zh else "Back to full access: Codex no longer uses the sandbox from config.toml (danger-full-access); "
                      "Agent J restarted, and the same conversation follows from your next message.")
    if ok:
        return False, ("config.toml 已改回全放行，但 Agent J 没能自己重启：在Agent J 主机上运行 `agentj service restart` 后生效。"
                       if zh else "config.toml is back to full access, but Agent J could not restart itself: run "
                       "`agentj service restart` on the Agent J host for it to take effect.")
    return False, ("没有回到全放行：config.toml 里仍有 sandbox_mode。在Agent J 主机上运行 `agentj codex-sandbox status` 查看。"
                   if zh else "Not back to full access: config.toml still holds a sandbox_mode. Run `agentj codex-sandbox "
                   "status` on the Agent J host.")


def _safe_name(v) -> str:
    v = v if isinstance(v, str) else ""
    return v[:40] if v and all(c.isalnum() or c in "-_." for c in v) else ""


def profile_text(prof: dict, zh: bool) -> str:
    """The selected [profiles.<p>] keeps its own sandbox_mode: not full access (one language)."""
    name, mode = _safe_name(prof.get("profile")), _safe_name(prof.get("mode"))
    where = f"[profiles.{name}]" if name else "[profiles.…]"
    if zh:
        return (f"没有回到全放行：config.toml 里选用的配置档 {where} 自己写了 sandbox_mode = {mode or '?'}，Codex 仍按它限制。"
                "Agent J 没有改这个配置档：在Agent J 主机上运行 `agentj codex-sandbox status` 查看，自己改那一档后运行 "
                "`agentj service restart`。")
    return (f"Not back to full access: the profile selected in config.toml, {where}, sets its own sandbox_mode = "
            f"{mode or '?'} and Codex still applies it. Agent J did not change that profile: run `agentj codex-sandbox "
            "status` on the Agent J host, edit that profile yourself, then run `agentj service restart`.")


def refusal_text(res: dict, lang: str) -> str | None:
    """A plain system page for a tap that changed nothing for a reason the owner has to act on (None otherwise)."""
    zh = lang != "en"
    if res.get("why") == "profile_restricts" and isinstance(res.get("profile"), dict):
        return profile_text(res["profile"], zh)
    if res.get("why") == "config_invalid" and res.get("detail") == "not_regular":
        return ("没有改：Codex 的 config.toml 是符号链接或不是普通文件（多半由你的 dotfiles 管理），Agent J 不替你改它。"
                "请自己去掉它指向的那个文件里顶层的 sandbox_mode，再运行 `agentj service restart`。" if zh else
                "Nothing changed: Codex's config.toml is a symlink or not a regular file (likely managed by your dotfiles), "
                "so Agent J does not edit it. Remove the top-level sandbox_mode in the file it points to yourself, then run "
                "`agentj service restart`.")
    return None


def deliver(host) -> bool:
    """serve.on_ready: the receipt of a tap made before the restart, once, as a system page; audited."""
    p = host.st.root / RECEIPT
    try:
        pending = json.loads(p.read_text())
    except FileNotFoundError:
        return False
    except (OSError, ValueError, UnicodeDecodeError):
        pending = None
    if isinstance(pending, dict) and pending.get("restart") == "requested" and pending.get("pid") == os.getpid():
        return False                    # the restart has not happened yet: the serve that comes back delivers it
    rec = take_receipt(host.st)
    if rec is None:
        return False
    ok, text = receipt_text(rec, getattr(host, "lang", "zh"))
    from . import codex_perm
    now = _mode(codex_perm.config_info())
    _audit(host.st, rec.get("device") if isinstance(rec.get("device"), str) else None,
           "receipt:" + ("ok" if ok else "not_full"), str(rec.get("before", "?"))[:40], now)
    host.agent_notice(text)
    return True
