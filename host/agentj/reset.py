"""P332 (PROTOCOL §33) 紧急修复, run by serve: ① the reset ladder and the host side of ② (Agent J Father help).

Plain executors — no running serve needed (the rescue watchdog imports them; each never raises):
    run_linger(st, *, runner=subprocess.run, system=platform.system, user=None) -> dict      {"ok", "why"?}
        Linux `loginctl enable-linger <this user>` (no sudo); "unavailable" elsewhere, "denied" when logind says no.
    run_rollback(st, *, runner=subprocess.run, current=None) -> dict                         {"ok", "why"?, "version"?}
        re-installs the newest verified wheel in <state>/upgrade-cache older than the running version (update.roll_back:
        hash re-checked, `--version` confirmed); "unavailable" when there is none. Does NOT restart serve.
    run_restart_serve(st, *, svc=None) -> dict                                                {"ok", "why"?}
        asks the service manager to restart the Agent J service (systemd: `--no-block`, so it is safe from serve itself
        and from another process); "not_installed" when there is no service.
    action_text(name, res, lang) -> str        the one plain sentence for an executor's result

The ladder (`ladder(ops, emit, finish, lang, steps)`) is pure: ops / emit / finish are injected; HostOps binds it to serve.
`Desk(host)` is serve's glue for the signed phone messages `reset` / `rescue_do` and `/reset` (one run at a time).
Texts never say relay / serve / harness / session id; technical words only go in `detail`.
"""
from __future__ import annotations

import asyncio
import contextlib
import functools
import getpass
import json
import platform
import re
import secrets
import shutil
import subprocess
import time

from . import controls, wire
from .text import pick

OK_AGENT = ("idle", "working", "waiting", "compacting")
RESCUE_ACTIONS = ("help", "report", "reconnect", "restart", "linger", "rollback", "new_session", "clear_friend_asks", "restart_session")
RESET_CMDS = ("reset",)
UNDO = "reset_undo.json"
RELAY_WAIT = 25.0        # s: the relay link back after a re-dial
DEVICE_WAIT = 15.0       # s: the asking remote back (so it hears the rest)
HEALTH_WAIT = 5.0        # s: the Agent's status settles after a step
_USER = re.compile(r"[A-Za-z0-9._][A-Za-z0-9._-]{0,63}")

STEP = {   # (run, ok, fail) per ladder step — plain words in the owner's language
    1: (("正在重新连接…", "Reconnecting…"), ("连接好了，Agent J 在线。", "Connected — Agent J is online."),
        ("重新连接后 Agent J 还是没回应，试下一步。", "Agent J still does not answer after reconnecting; trying the next step.")),
    2: (("正在找回对话…", "Finding your conversation…"), ("对话找回来了。", "Your conversation is back."),
        ("没找到原来的对话，试下一步。", "The conversation could not be found; trying the next step.")),
    3: (("正在开新对话…", "Starting a new conversation…"),
        ("新对话开好了，下一条消息从这里开始；之前的对话保存着，随时能找回。",
         "A new conversation is ready; your next message starts it. The old one is kept and can be brought back."),
        ("开新对话没成功，试下一步。", "Could not start a new conversation; trying the next step.")),
    4: (("正在重启Agent J 主机上的 Agent J…", "Restarting Agent J on the Agent J host…"),
        ("正在重启，大约半分钟后会自动连回来，然后就可以继续聊天了。",
         "Restarting — it reconnects by itself in about half a minute, then you can chat again."),
        ("重启没成功，请试试「请 Agent J Father 远程帮忙」。", "The restart did not work; try \"Ask Agent J Father for remote help\".")),
}
SKIP3 = ("这个对话开在Agent J 主机上的窗口里，新对话要在Agent J 主机上开，这一步跳过。",
         "This conversation lives in a window on the Agent J host; a new one has to be opened there, so this step is skipped.")
STOP = {"estop": ("连接好了；Agent J 现在是「全部停下」状态，点「恢复」后才会回复。",
                  "Connected; Agent J is stopped — tap Resume and it answers again."),
        "no_agent": ("连接好了，但Agent J 主机上还没选好 AI 助手，需要在Agent J 主机上设置一次。",
                     "Connected, but no AI assistant is chosen on the Agent J host yet; it has to be set up there once.")}
NOT_FIXED = ("还没修好，请试试「请 Agent J Father 远程帮忙」。", "Not fixed yet; try \"Ask Agent J Father for remote help\".")
WORDS = {   # executor results → plain sentences
    "linger": {True: ("已设好：没人登录时 Agent J 也会运行。", "Done: Agent J now runs even when nobody is logged in."),
               False: ("没设成，需要Agent J 主机主人在Agent J 主机上开一次（loginctl enable-linger）。",
                       "Not done: the Agent J host's owner has to turn it on there once (loginctl enable-linger).")},
    "rollback": {True: ("已退回上一个版本 {version}。", "Back on the previous version {version}."),
                 False: ("没能退回上一个版本。", "Could not go back to the previous version.")},
    "restart": {True: ("正在重启Agent J 主机上的 Agent J。", "Restarting Agent J on the Agent J host."),
                False: ("重启没成功。", "The restart did not work.")},
    "reconnect": {True: ("连接好了，Agent J 在线。", "Connected — Agent J is online."),
                  False: ("重新连接后 Agent J 还是没回应。", "Agent J still does not answer after reconnecting.")},
    "new_session": {True: ("新对话开好了，之前的对话保存着。", "A new conversation is ready; the old one is kept."),
                    False: ("开新对话没成功。", "Could not start a new conversation.")},
}
UNAVAILABLE = ("这台Agent J 主机上没有这一步可做。", "This step is not available on the Agent J host.")
RESTART_SCHEDULED = ("马上重启Agent J 主机上的 Agent J（报告发出后）。", "Agent J on the Agent J host restarts right after this report.")
OFFERABLE = ("reconnect", "restart", "linger", "rollback", "new_session")


def clean_offer(offer) -> list:
    """`offer` on reset_step / reset_done: only rescue_do action names, deduplicated, in order (§33 ②)."""
    if not isinstance(offer, (list, tuple)):
        return []
    out = []
    for a in offer:
        if isinstance(a, str) and a in OFFERABLE and a not in out:
            out.append(a)
    return out


def _t(lang, pair) -> str:
    return pick(lang, pair[0], pair[1])


def action_text(name: str, res: dict, lang: str = "zh") -> str:
    ok = bool(res.get("ok"))
    if not ok and res.get("why") == "unavailable":
        return _t(lang, UNAVAILABLE)
    pair = WORDS.get(name, {}).get(ok) or (("好了。", "Done.") if ok else ("没成功。", "It did not work."))
    return _t(lang, pair).format(version=res.get("version") or "")


# ------------------------------------------------------------------ plain executors (also for the rescue watchdog)
def run_linger(st, *, runner=subprocess.run, system=platform.system, user=None) -> dict:
    try:
        if system() != "Linux":
            return {"ok": False, "why": "unavailable"}
        exe = shutil.which("loginctl")
        user = user or getpass.getuser()
        if not exe or not _USER.fullmatch(user or ""):
            return {"ok": False, "why": "unavailable"}
        r = runner([exe, "show-user", user, "-p", "Linger", "--value"], capture_output=True, text=True, timeout=10,
                   stdin=subprocess.DEVNULL)
        if r.returncode == 0 and (r.stdout or "").strip() == "yes":
            return {"ok": True, "why": "already"}
        r = runner([exe, "enable-linger", user], capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL)
        return {"ok": True} if r.returncode == 0 else {"ok": False, "why": "denied", "detail": f"loginctl exit {r.returncode}"}
    except Exception as e:  # noqa: BLE001 — reported, never raised
        return {"ok": False, "why": "failed", "detail": type(e).__name__}


def previous_wheel(st, current: str | None = None):
    """(version, path) of the newest verified cached wheel older than `current`, else None."""
    from . import __version__, update
    cur = current or __version__
    best = None
    for p in (st.root / "upgrade-cache").glob("agentj-*-py3-none-any.whl"):
        v = p.name[len("agentj-"):-len("-py3-none-any.whl")]
        pv = update.parse(v)
        if pv is None or update.compare(v, cur) != -1 or not p.with_suffix(".verified.json").is_file():
            continue
        if best is None or pv > best[0]:
            best = (pv, v, p)
    return (best[1], best[2]) if best else None


def run_rollback(st, *, runner=subprocess.run, current=None) -> dict:
    try:
        from . import update
        found = previous_wheel(st, current)
        if not found:
            return {"ok": False, "why": "unavailable"}
        version, wheel = found
        ok = update.roll_back(st, update.install_kind(), wheel, version, runner)
        st.log("upgrade_rollback", status="ok" if ok else "failed", reason="rescue", version=version)
        return {"ok": True, "version": version} if ok else {"ok": False, "why": "failed", "version": version}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "why": "failed", "detail": type(e).__name__}


def run_restart_serve(st, *, svc=None) -> dict:
    try:
        if svc is None:
            from . import service as svc
        cur = svc.status()
        if not cur.get("installed"):
            return {"ok": False, "why": "not_installed"}
        if svc.platform() == "linux":
            svc._systemctl("--no-block", "restart", f"{svc.name()}.service", check=True)   # queued in systemd: survives us
        else:
            svc.restart()
        st.log("reset_restart", result="ok")
        return {"ok": True}
    except Exception as e:  # noqa: BLE001
        reason = getattr(e, "reason", None)
        return {"ok": False, "why": reason if isinstance(reason, str) else "failed", "detail": type(e).__name__}


def write_undo(st, kind: str, old: str) -> None:
    """<state>/reset_undo.json (0600, the state's private writer): the conversation a reset put aside."""
    st.write_private(st.root / UNDO, json.dumps({"kind": kind, "session": old, "at": int(time.time())}).encode())


# ------------------------------------------------------------------ the ladder (pure)
async def ladder(ops, emit, finish, lang: str = "zh", steps=(1, 2, 3, 4)) -> dict:
    """Each step runs only when the previous one did not bring the link back. ops: async reconnect() / repin() /
    new_session() → {"ok", "stop"?, "skip"?, "why"?, "detail"?, "old_session"?}, healthy() → bool, restart() → {"ok"}.
    emit(step, state, text, detail=None); finish(result) sends `reset_done` (step 4: BEFORE the restart)."""
    old = None

    async def call(fn):
        try:
            return await fn() or {}
        except Exception as e:  # noqa: BLE001 — one broken step escalates, never ends the run
            return {"ok": False, "detail": type(e).__name__}

    async def done(ok, step, text, **kw):
        res = {"ok": ok, "step": step, "text": text, **({"old_session": old} if old else {}), **kw}
        await finish(res)
        return res

    last = 0
    for n in steps:
        last = n
        run, good, bad = STEP[n]
        await emit(n, "run", _t(lang, run))
        if n == 4:
            await done(True, 4, _t(lang, good))
            r = await call(ops.restart)
            if r.get("ok"):
                return {"ok": True, "step": 4}
            await emit(4, "fail", _t(lang, bad), r.get("why") or r.get("detail"))
            return await done(False, 4, _t(lang, bad), why="unavailable")
        r = await call({1: ops.reconnect, 2: ops.repin, 3: ops.new_session}[n])
        if r.get("old_session"):
            old = r["old_session"]
        if r.get("stop"):
            text = _t(lang, STOP.get(r["stop"], NOT_FIXED))
            await emit(n, "ok" if r["stop"] in STOP else "fail", text, r.get("detail"))
            return await done(False, n, text, why=r["stop"])
        if r.get("skip"):
            await emit(n, "skip", _t(lang, SKIP3) if n == 3 else _t(lang, bad), r.get("why"))
            continue
        ok = bool(r.get("ok")) and (n == 1 or await call_bool(ops.healthy))
        await emit(n, "ok" if ok else "fail", _t(lang, good if ok else bad), r.get("detail") or r.get("why"))
        if ok:
            return await done(True, n, _t(lang, good))
    return await done(False, last, _t(lang, NOT_FIXED))


async def call_bool(fn) -> bool:
    try:
        return bool(await fn())
    except Exception:  # noqa: BLE001
        return False


async def new_session(host) -> dict:
    """Ladder step 3 alone: put the current conversation aside (kept in reset_undo.json) — the next message opens a fresh
    one, which the adapter pins as usual. A shared session (the owner's own window) is skipped: Agent J cannot open a
    window there."""
    a = host.agent
    if a is None:
        return {"ok": False, "why": "no_agent"}
    if (getattr(a, "cfg", None) or {}).get("session_mode") == "shared":
        return {"skip": True, "why": "shared"}
    if getattr(a, "status", None) in ("working", "compacting"):
        return {"ok": False, "why": "agent_working"}
    st, kind = host.st, a.kind
    old = st.agent_session(kind)
    if old:
        write_undo(st, kind, old)
    await a.drop_conversation()
    st.set_agent_session(kind, None)
    st.log("reset_new_session", agent=kind, result="ok")
    return {"ok": True, **({"old_session": old} if old else {})}


class HostOps:
    """The ladder's steps against a live serve Host."""

    def __init__(self, host, device: str | None = None, sleep=asyncio.sleep, clock=time.monotonic):
        self.h, self.device, self.sleep, self.clock = host, device, sleep, clock

    async def _until(self, cond, seconds) -> bool:
        end = self.clock() + seconds
        while not cond():
            if self.clock() >= end:
                return False
            await self.sleep(0.5)
        return True

    def _device_back(self) -> bool:
        return any(s.state == "ready" and s.p33 and s.device == self.device for s in list(self.h.sessions.values()))

    async def reconnect(self) -> dict:
        h = self.h
        ws = h.ws
        if ws is not None:                    # re-dial: relay_loop sees the close and connects again at once
            with contextlib.suppress(Exception):
                await asyncio.wait_for(ws.close(), 5)
            await self._until(lambda: h.ws is not ws, 5)
        if not await self._until(lambda: h.relay_up, RELAY_WAIT):
            return {"ok": False, "detail": "relay " + h.relay_state()}
        if self.device:
            await self._until(self._device_back, DEVICE_WAIT)
        status = h.eff_status()
        if status in OK_AGENT:
            return {"ok": self._ai_ready()}
        if status == "stopped":
            return {"stop": "estop", "detail": "agent stopped"}
        if status == "none":
            return {"stop": "no_agent", "detail": "no agent"}
        return {"ok": False, "detail": "agent " + str(status)}

    async def repin(self) -> dict:
        res = await self.h.agent_reconnect()
        return {"ok": bool(res.get("ok")), "detail": res.get("why")}

    async def new_session(self) -> dict:
        return await new_session(self.h)

    def _ai_ready(self) -> bool:
        a = self.h.agent
        if getattr(a, "kind", None) == "opencode":
            return getattr(a, "readiness", lambda: {"ready": False})().get("ready") is True
        return self.h.eff_status() in OK_AGENT

    async def healthy(self) -> bool:
        return await self._until(self._ai_ready, HEALTH_WAIT)

    async def restart(self) -> dict:
        await self.sleep(1.0)                 # let reset_done leave first
        return await asyncio.to_thread(run_restart_serve, self.h.st)


# ------------------------------------------------------------------ serve glue
class Desk:
    """`reset` / `rescue_do` (signed, §8 control-v1) and `/reset`. One run at a time; works during an e-stop (it only
    repairs connectivity — the stop switch stays on)."""

    def __init__(self, host):
        self.h = host
        self.busy: str | None = None
        self.task = None

    @staticmethod
    def target(t: str, obj: dict):
        if t == "reset":
            return "reset", {"what": "connection"}
        a = obj.get("action")
        if not isinstance(a, str) or (a not in RESCUE_ACTIONS and not re.fullmatch(r"ark_fix_[0-9a-f]{24}", a)):
            return None, None
        return a, {"action": a}

    async def on_phone(self, s, t: str, obj: dict) -> None:
        h, r = self.h, obj.get("r")
        if not wire.is_rid(r):
            return
        flow, target = self.target(t, obj)
        why = "shape" if target is None else controls.check(h.st, h.nonces, h.channel, s.device, obj, t, target)
        if not why and self.busy:
            why = "busy"
        if why:
            h.st.log("control_refused", device=s.device, action=t, reason=why)
            controls.log(h.st, action=t, device=s.device, result="refused:" + why, obj=target)
            return await h.send_app(s, {"t": "reset_done", "r": r, "flow": flow or "rescue", "ok": False, "why": why})
        controls.log(h.st, action=t, device=s.device, result="ok", obj=target, sig=obj.get("sig"), n=obj.get("n"),
                     ts=obj.get("ts"))
        self.start(s.device, r, flow)

    def start(self, device, r, flow) -> None:
        self.busy = flow
        self.h.st.log("reset_run", device=device, action=flow, result="start")
        self.task = self.h._bg(self._run(device, r, flow), "reset_" + flow)

    async def slash(self, s, authorization) -> tuple:
        """/reset from the ≡ menu or typed: signed like /update (the page signs action `reset`). → (text, kind)."""
        h, lang = self.h, self.h.lang
        auth = authorization if isinstance(authorization, dict) else {}
        why = controls.check(h.st, h.nonces, h.channel, s.device, auth, "reset", {"what": "connection"})
        controls.log(h.st, action="reset", device=s.device, result="refused:" + why if why else "ok",
                     obj={"what": "connection"}, sig=auth.get("sig"), n=auth.get("n"), ts=auth.get("ts"))
        if why:
            return pick(lang, "请在「设置 → 紧急修复」里点「重置连接」。", "Tap Reset connection in Settings → Emergency repair."), "refused"
        if self.busy:
            return pick(lang, "已经在修复中，请稍等。", "A repair is already running; please wait."), "info"
        r = auth.get("r") if wire.is_rid(auth.get("r")) else secrets.token_hex(8)
        self.start(s.device, r, "reset")
        return pick(lang, "正在重置连接，每一步都会显示在「设置 → 紧急修复」里。",
                    "Resetting the connection; each step shows in Settings → Emergency repair."), "info"

    async def _run(self, device, r, flow) -> None:
        h, lang = self.h, self.h.lang
        restart_after = False

        async def emit(step, state, text, detail=None, offer=None):
            m = {"t": "reset_step", "r": r, "flow": flow, "step": step, "state": state, "text": text}
            if detail:
                m["detail"] = str(detail)[:200]
            offer = clean_offer(offer)
            if offer:
                m["offer"] = offer
            await h._send_device(device, m)

        async def finish(res):
            await h._send_p33(lambda s: {"t": "reset_done", "r": r, "flow": flow, **res})

        ops = HostOps(h, device)
        try:
            if flow == "restart_session":
                from . import phone_restart
                await emit(1, "run", pick(lang, "正在重开 AI 会话…", "Reopening the AI session…"))
                res = await phone_restart.run(h)
                text = pick(lang, "AI 会话已重开，可以继续聊天，不需要到 Agent J 主机前。", "The AI session is ready. Continue chatting without going to the computer.") if res.get("ok") else pick(lang, "没能重开 AI 会话，请试「请 Agent J Father 远程帮忙」。", "Could not reopen the AI session. Try Ask Agent J Father for remote help.")
                if res.get("new"):
                    text = pick(lang, "原会话没能恢复，已开新会话并接好手机；之前的对话保存着，不需要到 Agent J 主机前。", "The old session could not resume. A new session is connected to your phone; the old conversation is kept. No need to go to the computer.")
                await emit(1, "ok" if res.get("ok") else "fail", text)
                await finish({**res, "step": 1, "text": text})
            elif flow in ("reset", "reconnect", "new_session", "restart"):
                steps = {"reset": (1, 2, 3, 4), "reconnect": (1, 2), "new_session": (3,), "restart": (4,)}[flow]
                await ladder(ops, emit, finish, lang, steps)
            elif flow in ("linger", "rollback"):
                await emit(1, "run", action_text_run(flow, lang))
                res = await asyncio.to_thread(run_linger if flow == "linger" else run_rollback, h.st)
                text = action_text(flow, res, lang)
                await emit(1, "ok" if res.get("ok") else "fail", text, res.get("why"))
                restart_after = flow == "rollback" and bool(res.get("ok"))
                if restart_after:
                    text += pick(lang, "正在重启Agent J 主机上的 Agent J…", " Restarting Agent J on the Agent J host…")
                await finish({"ok": bool(res.get("ok")), "step": 1, "text": text,
                              **({"why": res["why"]} if not res.get("ok") and res.get("why") == "unavailable" else {})})
            elif flow.startswith("ark_fix_"):
                from . import ark_plan_fix
                if h.agent and hasattr(h.agent, "_own_protocol") and h.agent._own_protocol() is False:
                    await finish({"ok":False,"why":"unavailable","text":pick(lang,"请在桌面更改你自己启动的 OpenCode。","Change the independently launched OpenCode on the desktop.")})
                else:
                    await emit(1,"run",pick(lang,"正在验证并重试正确的方舟套餐…","Checking and retrying the matching Ark plan…"))
                    res = await asyncio.to_thread(ark_plan_fix.apply, h.st, flow)
                    if res.get("ok") and not res.get("pending"):
                        if h.agent and h.agent.kind == "opencode":
                            why = await h.agent.apply_model(res["model"], None)
                            if why:
                                res = {"ok":False,"why":"model_failed"}
                        else:
                            restart_after = True
                    plan = "Agent Plan" if res.get("plan") == "agent" else "Coding Plan"
                    text = (pick(lang,f"已验证，正在切换到火山 {plan}…",f"Verified; switching to Volcano {plan}…") if res.get("pending") or restart_after else
                            pick(lang,f"已改用火山 {plan}。",f"Now using Volcano {plan}.") if res.get("ok") else
                            pick(lang,"未能改用另一套餐；原主模型仍保留。请重新检测，或检查目标套餐已有的 key 和服务商设置。","Could not switch plans; the original main model is kept. Check again, or review the target plan's existing key and provider settings."))
                    if res.get("line"):
                        text += "\n" + res["line"]
                    await finish({"ok":bool(res.get("ok")),"text":text,"pending":bool(res.get("pending")),**({"why":res["why"]} if res.get("why") else {})})
            elif flow == "clear_friend_asks":
                n = h.peers.dismiss_asks() if h.peers else 0
                await finish({"ok": True, "step": 1, "text": pick(lang, f"已拒绝并清除 {n} 张好友卡。", f"Dismissed {n} friend card(s).")})
            elif flow == "report":
                from . import rescue_kit
                await emit(1, "run", pick(lang, "正在检查Agent J 主机并把去掉隐私的报告发给 Agent J Father…",
                                          "Checking the Agent J host and sending a privacy-cleaned report to Agent J Father…"))
                ticket, _healthy, _err = await asyncio.to_thread(rescue_kit.report_only, lang, "report")
                text = (pick(lang, f"报告已发出，工单号 {ticket}。", f"Report sent, ticket {ticket}.") if ticket else
                        pick(lang, "报告没发出去，请稍后再试。", "The report could not be sent; try again later."))
                await emit(1, "ok" if ticket else "fail", text)
                await finish({"ok": bool(ticket), "step": 1, "text": text, **({"ticket": ticket} if ticket else {})})
            elif flow == "help":
                restart_after = await self._help(emit, finish, ops, lang)
        except Exception as e:  # noqa: BLE001 — the phone always gets an end
            h.st.log("reset_run", device=device, action=flow, result="error", exception_type=type(e).__name__)
            with contextlib.suppress(Exception):
                await finish({"ok": False, "step": 0, "text": _t(lang, NOT_FIXED)})
        finally:
            self.busy = None if not restart_after else self.busy
            h.st.log("reset_run", device=device, action=flow, result="end")
        if restart_after:
            await self._restart_after(device, flow, ops, emit, finish, lang)

    async def _restart_after(self, device, flow, ops, emit, finish, lang) -> None:
        """The restart a help / rollback run ends with (after its `reset_done` went). Its outcome as far as serve can know it
        before going down: a failed attempt → `reset_step` fail + a second `reset_done ok:false` (review P332 MEDIUM-5).
        `busy` is cleared whatever happens."""
        h = self.h
        try:
            res = await ops.restart()
        except Exception as e:  # noqa: BLE001
            res = {"ok": False, "why": "failed", "detail": type(e).__name__}
        finally:
            self.busy = None
        ok = bool(isinstance(res, dict) and res.get("ok"))
        h.st.log("reset_run", device=device, action=flow, result="restart_ok" if ok else "restart_failed")
        if not ok:
            text = _t(lang, STEP[4][2])
            with contextlib.suppress(Exception):
                await emit(4, "fail", text, (res or {}).get("why") or (res or {}).get("detail"))
            with contextlib.suppress(Exception):
                await finish({"ok": False, "step": 4, "text": text, "why": "restart_failed"})

    async def _help(self, emit, finish, ops, lang) -> bool:
        """② in a worker thread (rescue_kit.help_flow); its actions map to the same executors. A restart (asked by support
        or needed after a rollback) waits until the results are posted and `reset_done` has gone."""
        from . import rescue_kit
        loop, later = asyncio.get_running_loop(), []

        def on_loop(coro, timeout):
            return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout)

        def emit_t(step, state, text, offer=None):
            with contextlib.suppress(Exception):
                on_loop(emit(step, state, text, None, offer), 10)

        def act(name):
            # review P332 HIGH-1: support's word runs only what ① does anyway; rollback / linger / new_session are offered by
            # help_flow and run only on the owner's own signed `rescue_do <action>`
            if name not in rescue_kit.AUTO_ACTIONS:
                return False, _t(lang, UNAVAILABLE)
            if name == "restart":      # runs after reset_done: reported as scheduled, never as done before it is tried
                if name not in later:
                    later.append(name)
                return None, _t(lang, RESTART_SCHEDULED)
            if name == "reconnect":
                res = on_loop(ops.reconnect(), RELAY_WAIT + DEVICE_WAIT + 15)
                if not res.get("ok") and not res.get("stop"):
                    res = on_loop(ops.repin(), 30)
                res = {"ok": bool(res.get("ok")) and on_loop(ops.healthy(), HEALTH_WAIT + 5)}
            return bool(res.get("ok")), action_text(name, res, lang)

        res = await asyncio.to_thread(functools.partial(rescue_kit.help_flow, lang, emit_t, act, estop=self.h.stopped()))
        out = {k: res[k] for k in ("ok", "step", "text", "ticket", "category") if k in res}
        offer = clean_offer(res.get("offer"))
        if offer:
            out["offer"] = offer
        await finish(out)
        return bool(later)


def action_text_run(name: str, lang: str) -> str:
    return pick(lang, {"linger": "正在设置：没人登录时 Agent J 也运行…", "rollback": "正在退回上一个版本…"}.get(name, "正在处理…"),
                {"linger": "Setting Agent J to run when nobody is logged in…",
                 "rollback": "Going back to the previous version…"}.get(name, "Working…"))
