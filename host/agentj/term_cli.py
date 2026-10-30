"""P408 (ADR-P408-terminal H3 H6 M5, PROTOCOL §38): `agentj terminal status|enable|allow|deny|disable|forget|logs` and
serve's control.sock `terminal_cfg`. P414 (§38.13): `agentj terminal open [--cmd LINE]` / `output [--lines N]` — the Agent shows
the terminal (a line prefilled, never sent) and reads its agent-masked transcript (`terminal_go` / `terminal_read`, on
elevate.sock so they work inside the Agent fence; control.sock answers the same commands).

P417 #185: a computer-browser remote may also switch it on for itself (terminal.py `term_open` mode "enable": signature +
passkey + the computer login password checked by sudo); a remote that paired as a phone is never a default or a target here.
Widening (`enable`, `allow`) needs the owner AT the computer AND a passkey remote: the CLI refuses inside an Agent harness or
without a terminal; serve's `terminal_cfg` is ctl_auth.SENSITIVE (claim refused to any descendant of serve — the Agent,
tasks, the shared session, the web terminal itself — and to an Agent-marked environment) and wants a terminal on the caller's
stdin; then ONE `AgentJTerminal` host card on every ready remote shows a 6-digit match code, the devices and the minutes, and
only a remote with a passkey may allow it (Face ID; approval_level.HOST_CARDS — never auto-allowed). Narrowing (`disable`,
`deny`, `forget`) needs only the socket — or no serve at all (the files are changed directly).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import sys
import time

from . import term_store as store

OPS = ("status", "enable", "allow", "deny", "disable", "forget", "logs")
AGENT_OPS = ("open", "output")      # P414 (§37.13): not `terminal_cfg` — terminal_go / terminal_read
WIDEN = ("enable", "allow")
HOURS_DEFAULT, HOURS_MAX = 2, 24


def widening(req: dict) -> bool:
    return req.get("op") in WIDEN


def card_text(lang: str, op: str, code: str, names: list, minutes: int, hours: int | None, web: list) -> str:
    zh = not str(lang or "").lower().startswith("en")
    c = f"{code[:3]} {code[3:]}"
    if zh:
        lines = ["打开网页终端（这台 Agent J 主机上运行了 agentj terminal " + op + "）",
                 ("临时允许：" if op == "allow" else "能用的遥控器：") + "、".join(names),
                 f"每次最长 {minutes} 分钟" + (f"；{hours} 小时后自动取消" if hours else ""),
                 "打开后在电脑浏览器里敲的命令会在这台 Agent J 主机上运行，每次打开都要 Face ID。"]
        if web:
            lines.append("其中「" + "、".join(web) + "」是网页版：网页被篡改时可能借用 Face ID。")
        lines.append(f"对照码：{c}（Agent J 主机终端上应显示同一串数字；不一致或不是你本人操作就点拒绝）")
    else:
        lines = [f"Turn on the web terminal (agentj terminal {op} was run on this computer)",
                 ("Allowed for a while: " if op == "allow" else "Remotes that may use it: ") + ", ".join(names),
                 f"Up to {minutes} min per session" + (f"; ends by itself in {hours} h" if hours else ""),
                 "Commands typed in the computer browser run on this computer; every open asks for Face ID."]
        if web:
            lines.append("\"" + ", ".join(web) + "\" is the web version: a tampered page could borrow Face ID.")
        lines.append(f"Match code: {c} (the terminal shows the same digits; if not, or it isn't you, tap Deny)")
    return "\n".join(lines)


# ---------------------------------------------------------------- serve side (`terminal_cfg`)
async def ctl_cfg(desk, req: dict) -> dict:
    h, st = desk.h, desk.st
    op = req.get("op")
    if op not in OPS:
        return {"ok": False, "error": "shape"}
    cfg = await asyncio.to_thread(store.prune, st)
    if op == "status":
        devs = []
        for did, v in st.devices().items():
            e = store.entry(cfg, did)
            m = store.meta(st, did)
            devs.append({"id": did, "name": h._dname(did) or did, "passkey": bool(st.passkey_of_device(did)),
                         "web": store.is_web(st, did), "listed": bool(e), "until": (e or {}).get("until"),
                         "allow_web": bool(e and e["web"]), "cache": m["mode"] if m else None})
        return {"ok": True, "on": cfg["on"], "os": desk.supported(), "minutes": cfg["minutes"], "devices": devs,
                "open": desk.term is not None, "open_device": desk.term.device if desk.term else None}
    if op == "logs":
        try:
            lines = st.controls_path.read_text().splitlines()[-2000:]
        except OSError:
            lines = []
        rows = [r for r in (_json(x) for x in lines) if isinstance(r, dict) and str(r.get("action", "")).startswith("term_")]
        casts = sorted(p.name for p in (store.root(st) / "casts").glob("*.cast")) if (store.root(st) / "casts").is_dir() else []
        return {"ok": True, "log": rows[-50:], "casts": casts[-30:]}
    if op in ("disable", "deny", "forget"):
        dev = req.get("device")
        if op != "disable" and dev is not None and not isinstance(dev, str):
            return {"ok": False, "error": "shape"}
        if op == "disable":
            await asyncio.to_thread(store.write_config, st, {**cfg, "on": False})
            await asyncio.to_thread(store.forget, st)
            await desk.close("disabled")
        elif op == "deny":
            if not dev or not store.entry(cfg, dev):
                return {"ok": False, "error": "not_listed"}
            await asyncio.to_thread(store.write_config, st, {**cfg, "entries": [e for e in cfg["entries"] if e["device"] != dev]})
            await asyncio.to_thread(store.forget, st, dev)
            if desk.term is not None and desk.term.device == dev:
                await desk.close("disabled")
        else:
            n = await asyncio.to_thread(store.forget, st, dev)
            x = desk.term
            if x is not None and x.pw and (dev is None or x.device == dev):
                from .elevate import wipe
                wipe(x.pw)
                x.pw = None
        st.log("terminal_cfg", action=op, device=dev if isinstance(dev, str) else None)
        desk._log("terminal_cfg", None, op)
        desk._prefs()
        return {"ok": True, **({"deleted": n} if op == "forget" else {})}
    # widening: the host card
    if not desk.supported():
        return {"ok": False, "error": "os"}
    code = req.get("code")
    if not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code):
        return {"ok": False, "error": "shape"}
    if h.stopped():
        return {"ok": False, "error": "stopped"}
    devs = req.get("devices") if op == "enable" else [req.get("device")]
    if not isinstance(devs, list) or not all(isinstance(d, str) for d in devs):
        return {"ok": False, "error": "shape"}
    if not devs and op == "enable":
        # P417 #185: a remote that paired as a phone never uses the terminal — not a default, refused when named
        pks = [d for d in st.devices() if st.passkey_of_device(d) and store.device_kind(st, d) != "phone"]
        if len(pks) != 1:
            return {"ok": False, "error": "choose_device" if pks else "no_passkey"}
        devs = pks
    for d in devs:
        if d not in st.devices():
            return {"ok": False, "error": "unknown_device", "device": d}
        if not st.passkey_of_device(d):
            return {"ok": False, "error": "no_passkey", "device": d}
        if store.device_kind(st, d) == "phone":
            return {"ok": False, "error": "phone_device", "device": d}
    if op == "allow" and not cfg["on"]:
        return {"ok": False, "error": "disabled"}
    web_flag = req.get("web") is True
    webs = [d for d in devs if store.is_web(st, d)]
    if webs and not web_flag:
        return {"ok": False, "error": "web_device", "device": webs[0]}
    m = req.get("minutes")
    minutes = m if type(m) is int and 1 <= m <= store.MIN_MAX else cfg["minutes"]
    hrs = req.get("hours")
    hours = (hrs if type(hrs) is int and 1 <= hrs <= HOURS_MAX else HOURS_DEFAULT) if op == "allow" else None
    names = [h._dname(d) or d for d in devs]
    text = card_text(h.lang, op, code, names, minutes, hours, [h._dname(d) or d for d in webs])
    face = not all(faced(st, d) for d in devs)     # P573 (c): Face ID the first time a remote gets the terminal, then a button
    res = await host_card(h, text, {"op": op, "devices": devs, "minutes": minutes, "hours": hours, "web": web_flag, "code": code},
                          face=face)
    if res != "allow":
        return {"ok": False, "error": "denied" if res == "deny" else res}
    if face:
        await asyncio.to_thread(note_faced, st, devs)
    cfg = await asyncio.to_thread(store.prune, st)
    keep = [e for e in cfg["entries"] if e["device"] not in devs]
    until = int(time.time() + hours * 3600) if hours else None
    new = [{"device": d, "sudo": True, "web": d in webs, **({"until": until} if until else {})} for d in devs]
    await asyncio.to_thread(store.write_config, st, {"on": True if op == "enable" else cfg["on"], "entries": keep + new,
                                                    "minutes": minutes if op == "enable" else cfg["minutes"]})
    st.log("terminal_cfg", action=op, count=len(devs))
    desk._log("terminal_cfg", None, op)
    h.agent_notice(f"网页终端已在 Agent J 主机上{'打开' if op == 'enable' else '临时允许'}：{'、'.join(names)} / Web terminal "
                   f"{'turned on' if op == 'enable' else 'allowed for a while'} at the computer: {', '.join(names)}")
    desk._prefs()
    return {"ok": True, "devices": devs, "minutes": minutes, **({"until": until} if until else {})}


def _faced_path(st):
    from pathlib import Path
    return Path(st.root) / "terminal_faced.json"


def faced(st, device: str) -> bool:
    """P573 (c): this remote already turned the web terminal on once with Face ID, with the approval key it has now."""
    import hashlib
    import json
    try:
        d = json.loads(_faced_path(st).read_text())
    except (OSError, ValueError):
        return False
    sk = st.sign_key(device)
    return isinstance(d, dict) and bool(sk) and d.get(device) == hashlib.sha256(sk).hexdigest()


def note_faced(st, devices) -> None:
    import hashlib
    import json
    from .state import _write_private
    try:
        d = json.loads(_faced_path(st).read_text())
        d = d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        d = {}
    for dev in devices:
        sk = st.sign_key(dev)
        if sk:
            d[dev] = hashlib.sha256(sk).hexdigest()
    _write_private(_faced_path(st), json.dumps(d).encode())


async def host_card(h, text: str, target: dict, face: bool = True) -> str:
    """ONE `AgentJTerminal` card on every ready remote; the first signed answer decides. face (P573 c): Face ID — only a
    remote with a passkey may allow — the first time a remote gets the terminal; after that one signed button.
    → "allow" · "deny" · "timeout" · "estop" · "serve_stop" · "busy" · "no_device"."""
    from . import approvals
    from .serve import Ask
    from .terminal import CARD_TTL, TOOL
    if not h._approvers():
        return "no_device"
    if any(a.tool == TOOL and not a.fut.done() for a in h.asks.values()):
        return "busy"
    rid = secrets.token_hex(16)
    a = Ask(rid, TOOL, text, approvals.shown_digest(TOOL, text), target, approvals.input_digest(target),
            time.monotonic() + CARD_TTL, asyncio.get_running_loop().create_future(), require_passkey=face, pk_kind="terminal")
    h.asks[rid] = a
    h.st.log("ask", id=rid, tool=TOOL, agent="host", kind="terminal")
    h.activity("ask", id=rid, tool=TOOL, summary=text.split("\n", 1)[0])
    h.emit("ask", id=rid, tool=TOOL, summary=text)
    h._post(h._send_ready, lambda s: h._ask_msg(a, s))
    h.status_changed()
    h.push_notify("ask")
    try:
        await asyncio.wait({a.fut}, timeout=max(0.0, a.deadline - time.monotonic()))
        if not a.fut.done():
            a.fut.set_result(("timeout", None, None, None))
        decision, did, sk, sig = a.fut.result()
    finally:
        h.asks.pop(rid, None)
    if did:
        result, reason = ("allow" if decision == "allow" else "deny"), "device"
    else:
        result = reason = "timeout" if decision == "timeout" else ("estop" if h.stopped() else "serve_stop")
    approvals.record(h.st, rid=rid, agent="host", tool=TOOL, input_sha256=a.input_sha, shown_sha256=a.digest,
                     decision="allow" if result == "allow" else "deny", reason=reason, device=did if did else None,
                     sign_pub=sk, sig=sig)
    h.st.log("ask_done", id=rid, tool=TOOL, decision="allow" if result == "allow" else "deny", reason=reason, device=did)
    h.activity("decision", id=rid, tool=TOOL, result=result, reason=reason, by=h._dname(did) if did else None,
               summary=text.split("\n", 1)[0])
    h.emit("ask_done", id=rid, result=result, reason=reason, device=did)
    h._post(h._send_ready, lambda x: {"t": "ask_done", "id": rid, "result": result})
    h.status_changed()
    return result


def _json(line: str):
    try:
        return json.loads(line)
    except ValueError:
        return None


# ---------------------------------------------------------------- the CLI
ERR = {
    "os": "这台 Agent J 主机的系统暂不支持网页终端（现在支持 Linux、macOS 和 Windows 的 WSL；Windows 本身下一版）。/ Not on this system yet "
          "(Linux, macOS and WSL now; native Windows next).",
    "no_passkey": "没有设了通行密钥（Face ID / 指纹）的已配对遥控器：先在手机上「设置 → 安全」里开通行密钥。/ No paired remote with a passkey: "
                  "turn one on in the phone's Settings → Security first.",
    "choose_device": "有好几台带通行密钥的遥控器：用 --device 选一台（agentj terminal status 列出它们）。/ Several remotes have a passkey: "
                     "pick one with --device (listed by agentj terminal status).",
    "unknown_device": "没有这台遥控器（agentj terminal status 列出已配对的）。/ No such remote.",
    "web_device": "这台是网页版遥控器：网页被篡改时可能借用 Face ID。确定要用就加 --allow-web。/ This is the web version; add --allow-web if you "
                  "accept that a tampered page could borrow Face ID.",
    "disabled": "网页终端还没打开：先运行 agentj terminal enable。/ The web terminal is off: run agentj terminal enable first.",
    "denied": "手机上拒绝了，没有改动。/ Denied on the phone: nothing changed.",
    "timeout": "5 分钟内没人在手机上确认，没有改动。/ Nobody confirmed within 5 minutes: nothing changed.",
    "stopped": "已急停（全部停下）：先恢复。/ Everything is stopped: resume first.",
    "estop": "已急停（全部停下）：没有改动。/ Everything was stopped: nothing changed.",
    "busy": "已经有一张网页终端卡片在等手机确认。/ A web terminal card is already waiting on the phones.",
    "no_device": "现在没有在线的遥控器能确认：打开手机上的 Agent J 再试。/ No remote is online to confirm.",
    "not_listed": "这台遥控器不在名单上。/ That remote is not on the list.",
    "phone_device": "这台遥控器是手机：因为安全性和便捷性的原因，网页终端只能在电脑浏览器里操控。选一台电脑浏览器（--device）。/ "
                    "That remote is a phone; the web terminal can only be used from a computer browser — pick one with --device.",
}


def add_parser(sub) -> None:
    p = sub.add_parser("terminal", help="网页终端：status · enable · allow · deny · disable · forget · logs · open · output / the web "
                       "terminal (line mode)",
                       description="在电脑浏览器里开一个限时的终端（默认关）。在这台 Agent J 主机上运行 enable，或在电脑浏览器的「设置 → 安全」里"
                                   "用 Face ID + Agent J 主机登录密码开启；手机上不能用。 / A time-limited terminal in a computer browser (off by "
                                   "default): run enable here, or switch it on in the computer browser's Settings → Security with Face "
                                   "ID + the Agent J host login password; not on phones.")
    p.add_argument("action", choices=["status", "enable", "allow", "deny", "disable", "forget", "logs", "open", "output"])
    p.add_argument("target", nargs="?", help="遥控器（id 或名字），用于 allow、deny、forget / the remote (id or name) for allow, deny, forget")
    p.add_argument("--device", action="append", help="enable：能用的遥控器（可多次；默认 = 唯一有通行密钥的那台）/ a remote (repeatable)")
    p.add_argument("--minutes", type=int, help=f"enable：每次最长几分钟（1–{store.MIN_MAX}，默认 {store.MIN_DEFAULT}）/ minutes per session")
    p.add_argument("--hours", type=int, help=f"allow：几小时后自动取消（1–{HOURS_MAX}，默认 {HOURS_DEFAULT}）/ hours until it ends")
    p.add_argument("--allow-web", action="store_true", help="允许网页版遥控器 / allow a web-version remote")
    p.add_argument("--cmd", help="open：在手机终端的输入框里填好这一行（不会自动运行）/ prefill this line in the phone's terminal (never sent)")
    p.add_argument("--lines", type=int, help="output：最后几行（1–2000，默认 200）/ the last N lines")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_terminal)


def _resolve(st, ref: str | None) -> str | None:
    if not ref:
        return None
    devs = st.devices()
    if ref in devs:
        return ref
    from .state import device_names
    hits = [d for d, v in devs.items() if d.startswith(ref) or (isinstance(v, dict) and ref in (device_names(v)["name"],))]
    return hits[0] if len(hits) == 1 else ref


def _password_sudo() -> str | None:
    """None = a password sudo (what the terminal needs); else why not, in plain words."""
    import shutil
    import subprocess
    sudo = shutil.which("sudo")
    if not sudo:
        return "这台 Agent J 主机没有 sudo。/ This computer has no sudo."
    try:
        r = subprocess.run([sudo, "-k", "-n", "true"], capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL,
                           env={**os.environ, "LC_ALL": "C", "LANG": "C"})
    except (OSError, subprocess.SubprocessError):
        return "sudo 没有回应。/ sudo did not answer."
    if r.returncode == 0:
        return ("你的 sudo 不需要密码（NOPASSWD）：网页终端要用 Agent J 主机登录密码当第二道锁，这样配置不能开。/ Your sudo needs no password "
                "(NOPASSWD); the web terminal needs a password sudo.")
    if "password is required" not in (r.stderr or ""):
        return "你的账户不能用 sudo（不是管理员）：网页终端要一个用密码的 sudo。/ Your account cannot use sudo; a password sudo is needed."
    if sys.platform == "darwin":                     # P437: a standard (non-admin) account and Touch ID in sudo's PAM
        from .term_mac import enable_problem
        return enable_problem()
    return None


AGENT_ERR = {
    "disabled": "网页终端没打开（主人在 Agent J 主机上运行 agentj terminal enable）。/ The web terminal is off (the owner runs agentj terminal enable).",
    "nobody": "没有遥控器被允许用网页终端。/ No remote may use the web terminal.",
    "offline": "现在没有能用网页终端的遥控器在线（或它正显示一张确认卡）。/ No allowed remote is online right now (or it shows a card).",
    "rate": "刚打开过：隔 5 秒再试；10 分钟内最多 3 次，主人在终端里操作后重新计。/ Too soon: wait 5 s; at most 3 in 10 min until the owner acts.",
    "untrusted_source": "这一轮不是主人发起的（群聊 / 转发 / 好友），不能打开或读取终端。/ Not during a turn that is not the owner's.",
    "shape": "这一行不能用：太长、有控制字符、看不见的字符或一长串空格。/ The line is too long or has control / invisible characters.",
}


def _agent_cmd(a) -> None:
    """P414 (§37.13): `open` / `output` — any caller, also inside the Agent fence (elevate.sock)."""
    from . import elevate, names
    from .state import State
    st = State()
    if a.action == "open":
        req = {"t": "terminal_go", **({"line": a.cmd} if a.cmd else {})}
    else:
        req = {"t": "terminal_read", "lines": a.lines if a.lines is not None else 200}
    res = elevate.client_request(st, req, timeout=30, path=names.elevate_sock(st))
    if res.get("result") == "unavailable":
        try:
            res = names.ctl_call(st, {**{k: v for k, v in req.items() if k != "t"}, "cmd": req["t"]}, 30) if st.exists() else None
        except names.ServeBusy:
            sys.exit("✗ agentj serve 在运行但没有响应。/ agentj serve is running but not answering.")
        if res is None:
            sys.exit("✗ agentj serve 没在运行。/ agentj serve is not running.")
    if not res.get("ok"):
        sys.exit("✗ " + AGENT_ERR.get(res.get("error"), "没有完成 / not done") + f" ({res.get('error')})")
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    elif a.action == "open":
        print(f"✓ 已在 {res.get('reached', 0)} 台遥控器上打开网页终端" + ("，命令已填在输入框里，主人确认后才会运行" if a.cmd else "") +
              f"。/ Shown on {res.get('reached', 0)} remote(s)" + ("; the line waits in the input until the owner sends it." if a.cmd else "."))
    else:
        if not res.get("text"):
            print("（没有可读的终端记录：终端没开，或关闭已超过 30 分钟）/ (no terminal transcript: not open, or closed over 30 min ago)")
            return
        head = ("正在使用" if res.get("open") else "已关闭") + f" · {res.get('device_name') or ''}" + (" · 只显示最后一部分" if res.get("cut") else "")
        if res.get("hidden"):
            head += f" · [已隐藏 {res['hidden']} 处]"
        print(f"# {head}\n{res['text']}")


def cmd_terminal(a) -> None:
    from . import ctl_auth, names
    from .state import State
    if a.action in AGENT_OPS:
        return _agent_cmd(a)
    st = State()
    if not st.exists():
        sys.exit("✗ 还没初始化：先运行 agentj init。/ Not set up yet: run agentj init.")
    req = {"cmd": "terminal_cfg", "op": a.action}
    if a.action in WIDEN:
        from .terminal import platform_ok
        if not platform_ok():
            sys.exit("✗ " + ERR["os"])
        if set(os.environ) & set(ctl_auth.AGENT_ENV) or not sys.stdin.isatty():
            sys.exit("✗ 只有主人能在自己的终端里打开网页终端，Agent 和脚本做不了。/ Only the owner, typing in their own terminal, "
                     "can turn the web terminal on — not an Agent or a script.")
        why = _password_sudo()
        if why:
            sys.exit("✗ " + why)
        if not any(st.passkey_of_device(d) for d in st.devices()):
            sys.exit("✗ " + ERR["no_passkey"])
        code = f"{secrets.randbelow(10 ** 6):06d}"
        req.update(code=code, web=bool(a.allow_web))
        if a.action == "enable":
            req.update(devices=[_resolve(st, d) for d in (a.device or [])], minutes=a.minutes)
        else:
            dev = _resolve(st, a.target or (a.device or [None])[0])
            if not dev:
                sys.exit("✗ allow 要给一台遥控器：agentj terminal allow <id 或名字> / name a remote.")
            req.update(device=dev, hours=a.hours)
        print(f"已在已配对的手机上发出确认卡片，对照码 {code[:3]} {code[3:]}。请在有通行密钥的手机上核对后用 Face ID 确认（5 分钟内）。\n"
              f"A card went to your paired phones with match code {code[:3]} {code[3:]}. Confirm it with Face ID on a remote "
              f"that has a passkey (within 5 minutes).", flush=True)
    elif a.action in ("deny", "forget"):
        dev = _resolve(st, a.target or (a.device or [None])[0])
        if a.action == "deny" and not dev:
            sys.exit("✗ deny 要给一台遥控器：agentj terminal deny <id 或名字> / name a remote.")
        req["device"] = dev
    try:
        res = names.ctl_call(st, req, 400 if a.action in WIDEN else 30)
    except names.ServeBusy:
        sys.exit("✗ agentj serve 在运行但没有响应。/ agentj serve is running but not answering.")
    if res is None:
        if a.action in WIDEN:
            sys.exit("✗ agentj serve 没在运行，手机收不到卡片：先 agentj service start。/ agentj serve is not running.")
        res = _offline(st, req)
    if not res.get("ok"):
        if res.get("error") in ("claim_required", "claim_refused"):
            sys.exit("✗ 这个操作只能由主人在自己的终端里做。/ Only the owner, in their own terminal, can do this.")
        sys.exit("✗ " + ERR.get(res.get("error"), "没有改动 / nothing changed") + (f" ({res['device']})" if res.get("device") else ""))
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return
    _print(a.action, res)


def _offline(st, req: dict) -> dict:
    """serve is not running: narrowing and reading work on the files directly."""
    cfg = store.prune(st)
    op, dev = req["op"], req.get("device")
    if op == "disable":
        store.write_config(st, {**cfg, "on": False})
        store.forget(st)
    elif op == "deny":
        if not dev or not store.entry(cfg, dev):
            return {"ok": False, "error": "not_listed"}
        store.write_config(st, {**cfg, "entries": [e for e in cfg["entries"] if e["device"] != dev]})
        store.forget(st, dev)
    elif op == "forget":
        return {"ok": True, "deleted": store.forget(st, dev)}
    elif op == "status":
        devs = [{"id": d, "name": d, "passkey": bool(st.passkey_of_device(d)), "listed": bool(store.entry(cfg, d)),
                 "until": (store.entry(cfg, d) or {}).get("until"), "cache": (store.meta(st, d) or {}).get("mode")}
                for d in st.devices()]
        return {"ok": True, "on": cfg["on"], "minutes": cfg["minutes"], "devices": devs, "open": False}
    elif op == "logs":
        return {"ok": True, "log": [], "casts": sorted(p.name for p in (store.root(st) / "casts").glob("*.cast"))}
    return {"ok": True}


def _print(op: str, res: dict) -> None:
    if op == "status":
        print(("网页终端：开 / on" if res.get("on") else "网页终端：关 / off（agentj terminal enable 打开）") +
              f" · 每次最长 {res.get('minutes')} 分钟" + (" · 正在使用 / open now" if res.get("open") else ""))
        for d in res.get("devices") or []:
            mark = "✓" if d.get("listed") else "·"
            extra = []
            if d.get("until"):
                extra.append("临时，到 " + time.strftime("%m-%d %H:%M", time.localtime(d["until"])))
            if d.get("cache"):
                extra.append({"prf": "已记住 Agent J 主机登录密码（扫脸才能解开）", "local": "已记住 Agent J 主机登录密码（主机本地加密）"}.get(d["cache"], d["cache"]))
            if not d.get("passkey"):
                extra.append("没有通行密钥 / no passkey")
            print(f"  {mark} {d.get('name')} ({d.get('id')})" + (" — " + "；".join(extra) if extra else ""))
    elif op == "logs":
        for r in res.get("log") or []:
            print(f"  {time.strftime('%m-%d %H:%M:%S', time.localtime(r.get('ts', 0)))}  {r.get('action')}  {r.get('result')}  "
                  f"{r.get('device') or ''}")
        for c in res.get("casts") or []:
            print("  操作日志 / operation log: " + c)
    elif op in WIDEN:
        print("✓ 已确认：网页终端" + ("已打开" if op == "enable" else "已临时允许") + "。手机上「设置 → 安全 → 网页终端」进入。/ Confirmed: "
              "open it from the phone's Settings → Security → Web terminal.")
    elif op == "forget":
        print(f"✓ 已清除记住的 Agent J 主机登录密码（{res.get('deleted', 0)} 份）。/ Cached Agent J host login password(s) deleted.")
    else:
        print("✓ 已" + {"disable": "关闭网页终端并清除记住的密码", "deny": "从名单上去掉"}.get(op, "完成") + "。/ Done.")
