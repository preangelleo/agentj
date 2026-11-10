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
import signal
import subprocess
import time
from types import SimpleNamespace as NS

from . import controls, rescue_repair, wire
from .text import pick

OK_AGENT = ("idle", "working", "waiting", "compacting")
RESCUE_ACTIONS = ("help", "report", "reconnect", "restart", "linger", "rollback", "new_session", "clear_friend_asks", "restart_session",
                  "end_turn", "takeover",    # 0269 (#461): owner-signed only — never offered, never run by support
                  "stop_hard", "probe", "repair", "repair_cont", "deep_repair", "deep_repair_cont", "new_chat")   # #585: owner-signed only, never support
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
HEAL = {   # 0269: end_turn / takeover — (run, ok) and the failures
    "end_turn": (("正在强制结束这一轮…", "Ending this turn…"), ("这一轮已结束，可以接着发消息了。", "This turn is ended; you can send again.")),
    "takeover": (("正在接管卡住的会话…", "Taking over the stuck session…"),
                 ("接管好了：同一个对话接着用，排队的消息已递过去。", "Taken over: the same conversation goes on and queued messages were handed over.")),
    "estop": ("Agent J 现在是「全部停下」状态，点「恢复」后再接管。", "Agent J is stopped — tap Resume, then take over."),
    "foreign": ("卡住的那一轮在您自己开的程序里（比如终端里的 codex），Agent J 不会关掉它；请在 Agent J 主机上退出它后重发。",
                "The stuck turn runs in a program you started yourself (e.g. codex in a terminal); Agent J never closes it. "
                "Quit it on the Agent J host, then resend."),
    "fail": ("还没恢复，请再点一次「接管卡住的会话」，或试「请 Agent J Father 远程帮忙」。",
             "Not recovered yet; tap Take over again, or try Ask Agent J Father for remote help."),
}


# 0.26.10 #484 (ADR-0269-w5-rescue): every repair button first does what /stop does (serve.stop_turn: the adapter's own
# interrupt, then only the process Agent J started), and its last line says what is still wrong when no button can fix it.
UNSTICK = {
    "run": ("正在停下卡住的这一轮（和 /stop 一样）…", "Stopping the stuck turn (same as /stop)…"),
    "ok": ("卡住的这一轮已停下，排队的消息会接着发给 AI。", "The stuck turn is stopped; queued messages go on to the AI."),
    "interrupted": ("这一轮已按「停下」结束（AI 自己收了尾），排队的消息会接着发。",
                    "The turn ended on the stop request (the AI wrapped up itself); queued messages go on."),
    "children": ("这一轮卡在一个停不下来的命令上，Agent J 只结束了那个命令，这一轮已收尾，排队的消息会接着发。",
                 "The turn was stuck on a command that would not stop; Agent J ended only that command, the turn wrapped "
                 "up, and queued messages go on."),
    "released": ("Agent J 主机上 Codex 的那一轮早就不动了（很久没有任何进展，也没在等批准），不再等它，您的消息接着发。",
                 "The Codex turn on the Agent J host had made no progress for a long time and was not waiting for an approval; "
                 "Agent J stopped waiting for it and your message goes on."),
    "desktop_live": ("Agent J 主机上您自己打开的 Codex（终端或桌面版）正在跑一轮，Agent J 不会关掉它。等它跑完，或在 Agent J 主机上"
                     "按 Esc 停下、关掉那个 codex 终端，您的消息就会发出。",
                     "Codex that you opened yourself on the Agent J host (terminal or desktop app) is running a turn; Agent J "
                     "never closes it. When it finishes, or after you press Esc there or close that codex terminal, your "
                     "message goes out."),
    "fail": ("这一轮没停下来。请点「接管卡住的会话」。", "The turn did not stop. Tap Take over the stuck session."),
}
QUOTA = {   # the AI service says no: no button here changes that (#484: never promise it)
    "rate_limit": ("连接和 AI 会话都正常，但 {name} 的用量额度用完了，重置和重开都帮不了。{when}也可以点顶部的模型名换一个模型。",
                   "The connection and the AI session are fine, but {name}'s usage limit is used up; resetting or reopening "
                   "cannot change that. {when}You can also tap the model name at the top to pick another model."),
    "balance": ("连接和 AI 会话都正常，但 {name} 的余额或额度不够了，重置和重开都帮不了。充值后重发，或点顶部的模型名换一个模型。",
                "The connection and the AI session are fine, but {name}'s balance or credit has run out; resetting or "
                "reopening cannot change that. Top up and resend, or tap the model name at the top to pick another model."),
    "when_at": ("额度 {at} 恢复，到时 Agent J 会自动接着做。", "It resets at {at}, and Agent J then continues by itself. "),
    "when": ("等额度恢复后重发。", "Resend after it resets. "),
}
NOACK = ("Codex 没有接收新消息（它的后台程序对新的一轮没有任何回应）。Agent J 已关掉自己启动的那个 Codex 后台程序，下一条消息会换一个新的。",
         "Codex did not take the new message (its background program never answered the new turn). Agent J closed the Codex "
         "background program it had started; the next message gets a new one.")
NOACK_NEXT = ("如果下一条还是这样，在 Agent J 主机终端运行 agentj doctor。", "If the next one does the same, run agentj doctor on the Agent J host.")
UNSTICK_WAIT = 3.0       # s for the turn to end after the /stop step (the adapter's halt already waited for its process)
SOFT_WAIT = 5.0          # s for the harness to end the turn itself after its soft interrupt / after its tool children ended


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
    from .auto_update import signed_cached_wheel
    cur = current or __version__
    best = None
    for p in (st.root / "upgrade-cache").glob("agentj-*-py3-none-any.whl"):
        v = p.name[len("agentj-"):-len("-py3-none-any.whl")]
        pv = update.parse(v)
        if pv is None or update.compare(v, cur) != -1 or signed_cached_wheel(st, v) != p:
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
        from .auto_update import signed_cached_wheel
        if signed_cached_wheel(st, version) != wheel:
            return {"ok": False, "why": "unavailable"}
        ok = update.roll_back(st, update.install_kind(), wheel, version, runner, signed_only=True)
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
        if a.kind == "claude" and getattr(a, "session", None) is None:
            # The owner's existing repair button can bootstrap the managed shared process remotely.
            return await host.agent_reconnect()
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


def _desktop_waiting(a) -> bool:
    return bool(getattr(a, "desktop_wait", False)) and not getattr(a, "phone_turn", False) and hasattr(a, "release_stuck")


async def desktop_dead(a) -> bool | None:
    """The shared-Codex desktop turn a phone message waits behind is dead (turn_heal.desktop_stuck); None = cannot say."""
    from . import turn_heal
    path = getattr(a, "wait_path", None) or getattr(a, "rollout", None)
    if path is None:
        return None
    sid = getattr(a, "wait_sid", None) or getattr(a, "rollout_sid", None) or (getattr(a, "cfg", None) or {}).get("shared_session_id")
    own = getattr(a, "_own_pids", lambda: ())()
    try:
        return bool(await asyncio.to_thread(turn_heal.desktop_stuck, path, own, sid))
    except Exception:  # noqa: BLE001
        return None


async def unstick(h, device, sleep=asyncio.sleep, clock=time.monotonic) -> dict:
    """#484: before a repair step, do what /stop does — never wait behind the running turn. → {"ran": bool, "how"?, "ok"?,
    "why"?}. A phone message waiting behind a dead desktop turn (shared Codex) is let through (release_stuck, L-6 kept by
    wait_desktop); a live desktop turn in the owner's own Codex is never touched ("desktop_live"); our own turn → serve's
    stop_turn (the adapter's interrupt, then only the process Agent J started); still busy → turn_heal takeover."""
    a = getattr(h, "agent", None)
    if a is None:
        return {"ran": False}
    if _desktop_waiting(a):
        dead = await desktop_dead(a)
        if not dead:
            h.st.log("rescue_unstick", agent=a.kind, result="desktop_live")
            return {"ran": True, "ok": False, "why": "desktop_live"}
        a.release_stuck()
        end = clock() + 10
        while getattr(a, "desktop_wait", False) and clock() < end:
            await sleep(0.2)
        h.st.log("rescue_unstick", agent=a.kind, result="released")
        return {"ran": True, "ok": True, "how": "released"}
    if getattr(a, "status", None) not in ("working", "compacting"):
        return {"ran": False}
    heal = getattr(h, "turn_heal", None)
    ends0 = getattr(heal, "ends", 0)

    def ended():
        b = getattr(h, "agent", None)
        return b is not a or getattr(heal, "ends", 0) != ends0 or getattr(a, "status", None) not in ("working", "compacting")

    async def settle(secs):
        end = clock() + secs
        while not ended() and clock() < end:
            await sleep(0.1)
        return ended()

    from .agent import Agent
    if type(a).halt is Agent.halt:
        # RESEARCH-infinity ① (ADR-0269-w5-rescue): ① the harness's own soft interrupt (Codex turn/interrupt, Claude control
        # interrupt) — it counts only when the harness itself ends the turn (turn_done is never forced here);
        # ② only that turn's tool children (started by its harness after the turn began, ours by the parent chain);
        # ③ /stop: the app-server / process Agent J started. The next message resumes the same conversation.
        with contextlib.suppress(Exception):
            await a.interrupt_request(getattr(a, "proc", None))
        if await settle(SOFT_WAIT):
            h.st.log("rescue_unstick", agent=a.kind, result="interrupted")
            return {"ran": True, "ok": True, "how": "interrupted"}
        if await _stop_turn_children(h, a) and await settle(SOFT_WAIT):
            h.st.log("rescue_unstick", agent=a.kind, result="children")
            return {"ran": True, "ok": True, "how": "children"}
    await h.stop_turn(device or "owner", answered=True)          # exactly /stop (serve.stop_turn)
    if await settle(UNSTICK_WAIT):
        h.st.log("rescue_unstick", agent=a.kind, result="stopped")
        return {"ran": True, "ok": True, "how": "stopped"}
    if heal is None:
        return {"ran": True, "ok": False, "why": "not_stopped"}
    res = await heal.heal(device or "owner", None, action="takeover")
    h.st.log("rescue_unstick", agent=a.kind, result="takeover_" + ("ok" if res.get("ok") else str(res.get("why") or "failed")))
    if res.get("ok") or ended():
        return {"ran": True, "ok": True, "how": "takeover"}
    return {"ran": True, "ok": False, "why": res.get("why") or "not_stopped"}


async def _stop_turn_children(h, a) -> int:
    """SIGTERM the running turn's tool children only (turn_heal.turn_children of the harness Agent J started, each checked by
    may_stop and re-checked by signal_same) → how many were signalled. Never the harness itself, never a foreign process."""
    from . import turn_heal
    p = getattr(a, "proc", None)
    if p is None or getattr(p, "returncode", 0) is not None:
        return 0
    if not getattr(h, "turn_t0", None):
        return 0      # security #484 note 1: no known turn start → 「this turn's children」 would include older background work
    try:
        rows = await asyncio.to_thread(turn_heal.proc_rows)
        since = turn_heal.TurnHeal._turn_t0(NS(h=h))
        n = 0
        for pid in turn_heal.turn_children([p.pid], since, rows):
            start = (rows.get(pid) or (None, None))[1]
            if not isinstance(start, (int, float)) or start < since:
                continue  # Unknown or pre-turn children are never repair targets.
            if turn_heal.may_stop(h.st, pid, rows=rows) and turn_heal.signal_same(pid, rows.get(pid), signal.SIGTERM):
                n += 1
        return n
    except Exception:  # noqa: BLE001 — falls through to the /stop step
        return 0


def quota_state(h) -> dict | None:
    """The last turn ended on the AI service's limit (auth_rescue's classified signal, set during or after that turn's
    start) → {"cls", "name", "at"?}; None otherwise."""
    a = getattr(h, "agent", None)
    ar = getattr(h, "auth_rescue", None)
    if a is None or ar is None:
        return None
    rec = (getattr(ar, "state", None) or {}).get(a.kind)
    if not isinstance(rec, dict) or rec.get("cls") not in ("rate_limit", "balance"):
        return None
    started = (getattr(h, "turn_at", None) or (0.0,))[0] or 0.0
    if not isinstance(rec.get("at"), (int, float)) or rec["at"] < started - 2:
        return None
    from .auth_rescue import LABEL
    out = {"cls": rec["cls"], "name": LABEL.get(a.kind, a.kind)}
    hit = getattr(a, "limit_hit", None)
    if isinstance(hit, dict) and isinstance(hit.get("at"), (int, float)) and hit["at"] > time.time():
        out["at"] = hit["at"]
    return out


def quota_text(q: dict, lang: str) -> str:
    when = (_t(lang, QUOTA["when_at"]).format(at=time.strftime("%H:%M", time.localtime(q["at"]))) if q.get("at")
            else _t(lang, QUOTA["when"]))
    return _t(lang, QUOTA[q["cls"]]).format(name=q["name"], when=when)


def noack_text(h, lang: str) -> str | None:
    """The adapter's last turn/start was never answered (CodexAgent.noack_at, after that turn's start) → the plain line,
    with the Codex CLI / daemon version fix when they differ (doctor's check_codex_daemon)."""
    a = getattr(h, "agent", None)
    at = getattr(a, "noack_at", None)
    started = (getattr(h, "turn_at", None) or (0.0,))[0] or 0.0
    if not isinstance(at, (int, float)) or at < started - 2:
        return None
    return _t(lang, NOACK) + (" " if lang == "en" else "") + (codex_fix(h, lang) or _t(lang, NOACK_NEXT))


def codex_fix(h, lang: str) -> str | None:
    """doctor's check_codex_daemon fix line (the Codex CLI Agent J starts and the owner's shared daemon differ), in the
    owner's language; None when they match or it cannot say. Read-only (one `initialize` / the release link)."""
    with contextlib.suppress(Exception):
        from . import doctor, service
        row = doctor.check_codex_daemon(h.st, service.status())
        if row and row.get("status") == doctor.WARN and row.get("hint"):
            zh, _, en = row["hint"].partition(" / ")
            return en if lang == "en" and en else zh
    return None


def unstick_key(un: dict) -> str:
    if un.get("ok"):
        return un["how"] if un.get("how") in ("released", "interrupted", "children") else "ok"
    return "desktop_live" if un.get("why") == "desktop_live" else "fail"


async def after_text(h, un: dict, text: str, lang: str) -> tuple[str, str | None]:
    """The repair's last words: what the /stop step did, the step's own result, then what no button can fix. → (text, why).
    why: desktop_live · foreign · not_stopped · quota · noack (None = nothing left that the owner must act on)."""
    lines, why = [], None
    if un.get("ran"):
        key = unstick_key(un)
        line = HEAL["foreign"] if un.get("why") == "foreign" else UNSTICK[key]
        lines.append(_t(lang, line))
        if not un.get("ok"):
            why = "desktop_live" if un.get("why") == "desktop_live" else "foreign" if un.get("why") == "foreign" else "not_stopped"
        if why == "desktop_live":
            fix = await asyncio.to_thread(codex_fix, h, lang)
            if fix:
                lines.append(fix)
    if text:
        lines.append(text)
    q = quota_state(h)
    if q:
        lines.append(quota_text(q, lang))
        why = why or "quota"
    na = await asyncio.to_thread(noack_text, h, lang)
    if na:
        lines.append(na)
        why = why or "noack"
    return "\n".join(lines), why


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


# ------------------------------------------------------------------ #585: buttons that prove what they promise
P585_FLOWS = ("stop_hard", "probe", "repair", "repair_cont", "deep_repair", "deep_repair_cont", "new_chat", "new_session", "restart")
TAKE_WHY = {   # 「接管」's real reason and what to do next (chairman: never a silent failure)
    "estop": ("现在是「全部停下」状态，接管不会动它。点下面的「恢复」，Agent J 会接着自动接管。",
              "Agent J is stopped (Stop everything), so take-over does nothing. Tap Resume below and Agent J takes over by itself."),
    "busy": ("另一个修复正在进行，请等它做完再点。", "Another repair is running; wait for it to finish, then tap again."),
    "no_agent": ("Agent J 主机上还没选好 AI 助手，没有可接管的会话。", "No AI assistant is set up on the Agent J host; there is no session to take over."),
    "foreign": HEAL["foreign"],
    "not_recovered": ("已经打断了这一轮，但排队的消息没能送到 AI，会话还没恢复。下一步：点「重启 Agent」（会重新拉起 AI 程序，对话保留）。",
                      "The turn was interrupted, but the queued message did not reach the AI; the session is not back yet. "
                      "Next: tap Restart Agent (it starts the AI program again; the conversation is kept)."),
}
TAKE_NEXT = {"foreign": "restart", "not_recovered": "restart_session", "busy": "", "no_agent": ""}
RESTART_RUN = ("正在结束 Agent J 的全部进程并重启服务…", "Ending every Agent J process and restarting the service…")
RESTART_WAIT = ("服务正在重启，连上后会自动确认 Agent 能回话，不用再点。", "The service is restarting; once connected it checks by itself that the Agent answers — no need to tap again.")
RESTART_NONE = ("这台 Agent J 主机上的 Agent J 不是作为后台服务运行的，没法从手机重启。", "Agent J on this host does not run as a background service, so it cannot be restarted from the phone.")
RESTART_FAIL = ("服务没能重启。", "The service did not restart.")
RESTARTED = ("服务已经重启好了（换成了新的进程）。", "The service restarted (it is a new process now).")
NOT_RESTARTED = ("服务没有真的重启（还是原来的进程）。", "The service did not really restart (it is still the same process).")
LADDER = {1: ("停止", "Stop"), 2: ("重启 Agent", "Restart Agent"), 3: ("重启服务", "Restart service"), 4: ("换个新对话", "New conversation")}
REPAIR_RUN = {1: ("① 正在停下卡住的一切（这一轮、子 Agent、命令、没答完的确认）…", "① Stopping everything that is stuck (the turn, sub-agents, commands, unanswered cards)…"),
              2: ("② 正在重启 Agent（重新拉起 AI 程序，对话保留）…", "② Reopening the AI session (starting the AI program again; conversation kept)…"),
              3: ("③ 正在重启服务…", "③ Restarting the service…"),
              4: ("④ 正在换个新对话（原来的对话保存着）…", "④ Starting a new conversation (the old one is kept)…")}
STOPPED_WAIT = ("现在是「全部停下」状态。点下面的「恢复」，一键修复会自动接着做，不用再点。",
                "Stop everything is on. Tap Resume below and One-tap repair goes on by itself; no need to tap again.")
NEW_OK = ("新对话开好了；原来的对话保存着，随时能找回。", "A new conversation is ready; the old one is kept and can be brought back.")
NEW_SHARED = ("这个对话开在 Agent J 主机上你自己的窗口里，新对话要在那台电脑上开。可以点「重启 Agent」。",
              "This conversation lives in your own window on the Agent J host; a new one has to be opened there. You can tap Restart Agent.")
NEW_FAIL = ("没能开新对话。", "Could not start a new conversation.")
ALL_FAIL = ("四步都试过了，Agent 还是不回话。可以把去掉隐私的诊断发给客服，请他们远程看看（发之前会再问你一次）。",
            "All four steps were tried and the Agent still does not answer. You can send a privacy-cleaned diagnosis to "
            "support so they can look remotely (you are asked once more before it is sent).")


def _summary(steps, lang) -> str:
    marks = [(_t(lang, LADDER[n]) + (" ✓" if ok else " ✗")) for n, ok in steps]
    return pick(lang, "做了：", "Done: ") + (" → ".join(marks) if marks else "—")


class _Rescue585:
    """#585 flows of Desk (stop_hard · probe · repair · repair_cont · new_chat / new_session · restart)."""
    async def _p585(self, device, r, flow, emit, finish, ops, lang) -> bool:
        h = self.h
        if flow in ("deep_repair", "deep_repair_cont"):
            original_finish = finish
            async def finish(res):
                if res.get("pending"):
                    await original_finish(res)
                else:
                    await original_finish({**res, **await self._deep_report(lang, res)})
        if flow == "deep_repair":
            # Serve is responsive: the watchdog's takeover is not active. Still use the exact
            # signed owner action and the published-wheel verifier, never a phone-supplied URL.
            from . import update
            await emit(1, "run", pick(lang, "正在核验并重装 Agent J…", "Verifying and reinstalling Agent J…"))
            rescue_repair.write_marker(h.st, "deep_repair", r, device)
            installed = await asyncio.to_thread(update.repair_install, h.st)
            if installed.get("result") != "ok":
                with contextlib.suppress(OSError):
                    (h.st.root / rescue_repair.MARKER).unlink()
                await finish({"ok": False, "step": 1, "why": "repair_failed", "text": pick(lang, "重装未完成。", "Reinstall did not complete."),
                              "rollback": bool(previous_wheel(h.st))})
                return False
            await emit(1, "ok", pick(lang, "重装完成。", "Reinstallation finished."))
            if installed.get("service") == "restart_scheduled":
                await finish({"ok": True, "step": 2, "pending": True, "cont": "deep_repair_cont",
                              "text": pick(lang, "正在等待服务恢复，随后检查 AI 会话。", "Waiting for the service to return, then checking the AI session.")})
                return False
            with contextlib.suppress(OSError):
                (h.st.root / rescue_repair.MARKER).unlink()
            return await self._deep_finish(emit, finish, lang)
        if flow == "deep_repair_cont":
            marker = rescue_repair.take_marker(h.st)
            if (not marker or marker.get("flow") != "deep_repair" or marker.get("device") != device
                    or not marker.get("restarted")):
                await finish({"ok": False, "step": 2, "why": "unavailable", "text": _t(lang, NOT_FIXED)})
                return False
            return await self._deep_finish(emit, finish, lang)
        if flow == "stop_hard":
            await emit(1, "run", _t(lang, REPAIR_RUN[1])[2:])
            res = await rescue_repair.stop_hard(h, device)
            text = rescue_repair.stop_text(res, lang)
            await emit(1, "ok" if res.get("ok") else "fail", text, res.get("why"))
            extra = {} if res.get("ok") else {"next": "restart" if res.get("why") in ("foreign", "still_running", "not_stopped") else "repair"}
            await finish({"ok": bool(res.get("ok")), "step": 1, "text": text, **({"why": res["why"]} if res.get("why") else {}), **extra})
            return False
        if flow == "probe":
            rec = rescue_repair.probe_marker(h.st)
            lines = [_t(lang, RESTARTED if rec.get("restarted") else NOT_RESTARTED)] if rec else []
            await emit(1, "run", _t(lang, rescue_repair.PROBE_RUN))
            pr = await rescue_repair.probe(h)
            lines.append(rescue_repair.probe_line(pr, lang))
            await emit(1, "ok" if pr.get("ok") else "fail", lines[-1], pr.get("why"))
            await finish({"ok": bool(pr.get("ok")), "step": 1, "text": "\n".join(lines),
                          **({} if pr.get("ok") else {"why": pr.get("why") or "no_reply", **self._next_for(pr, "repair")})})
            return False
        if flow in ("new_chat", "new_session"):
            await self._new_chat(device, 1, emit, lang, finish_it=finish)
            return False
        if flow == "restart":
            await self._restart(device, r, flow, 1, emit, finish, lang, cont="probe", steps=[])
            return False
        if flow == "repair":
            steps = []
            estop0 = h.stopped()
            await emit(1, "run", _t(lang, REPAIR_RUN[1]))
            res = await rescue_repair.stop_hard(h, device)
            await emit(1, "ok" if res.get("ok") else "fail", rescue_repair.stop_text(res, lang), res.get("why"))
            if res.get("why") == "no_agent":
                await finish({"ok": False, "step": 1, "why": "no_agent", "text": rescue_repair.stop_text(res, lang)})
                return False
            if estop0:      # security #585-1/2: a repair never lifts Stop everything itself — the owner's own Resume does,
                # then the page sends `repair` again (a fresh signature) and the ladder goes on from a clean stop
                text = rescue_repair.stop_text(res, lang) + "\n" + _t(lang, STOPPED_WAIT)
                await finish({"ok": False, "step": 1, "why": "estop", "resume": True, "cont": "repair", "text": text})
                return False
            if await self._probe_ok(1, emit, lang):
                steps.append((1, True))
                return await self._repaired(finish, steps, 1, lang)
            steps.append((1, False))
            await emit(2, "run", _t(lang, REPAIR_RUN[2]))
            from . import phone_restart
            a0 = h.agent
            release = bool(a0 is not None and _desktop_waiting(a0) and await desktop_dead(a0))   # as the single button does
            try:
                pres = await phone_restart.run(h, release=release)
            except Exception as e:  # noqa: BLE001
                pres = {"ok": False, "why": type(e).__name__}
            await emit(2, "ok" if pres.get("ok") else "fail",
                       pick(lang, "AI 程序已重新拉起。", "The AI program was started again.") if pres.get("ok") else
                       pick(lang, "AI 程序没能重新拉起。", "The AI program could not be started again."), pres.get("why"))
            if pres.get("ok") and await self._probe_ok(2, emit, lang):
                steps.append((2, True))
                return await self._repaired(finish, steps, 2, lang)
            steps.append((2, False))
            if await self._restart(device, r, "repair", 3, emit, finish, lang, cont="repair_cont", steps=steps):
                return False
            steps.append((3, False))
            return await self._step4(device, finish, emit, steps, lang)
        if flow == "repair_cont":
            rec = rescue_repair.take_marker(h.st) or {}
            steps = [tuple(x) for x in rec.get("steps") or [] if isinstance(x, list) and len(x) == 2 and x[0] in LADDER
                     and isinstance(x[1], bool)]
            restarted = bool(rec.get("restarted"))
            await emit(3, "ok" if restarted else "fail", _t(lang, RESTARTED if restarted else NOT_RESTARTED))
            if restarted and await self._probe_ok(3, emit, lang):
                steps.append((3, True))
                return await self._repaired(finish, steps, 3, lang)
            steps.append((3, False))
            return await self._step4(device, finish, emit, steps, lang)
        return False


    async def _deep_report(self, lang, result):
        """Send a bounded, redacted host diagnostic after the signed repair."""
        from . import rescue_kit
        why = result.get("why")
        reason = why if why in ("repair_failed", "session_failed", "no_reply", "estop") else "unknown"
        try:
            ticket, _, _ = await asyncio.to_thread(rescue_kit.report_only, lang, "deep_repair_" + reason)
            return {"ticket": ticket} if ticket else {}
        except Exception:  # noqa: BLE001 — support availability never authorizes or blocks repair
            return {}

    async def _deep_finish(self, emit, finish, lang) -> bool:
        from . import phone_restart
        await emit(3, "run", pick(lang, "正在恢复当前 AI 会话…", "Restarting the current AI session…"))
        res = await phone_restart.run(self.h)
        if not res.get("ok"):
            await finish({"ok": False, "step": 3, "why": "session_failed",
                          "text": pick(lang, "AI 会话尚未恢复。", "The AI session has not recovered."),
                          "rollback": bool(previous_wheel(self.h.st))})
            return False
        await emit(3, "ok", pick(lang, "AI 会话已重启。", "The AI session restarted."))
        pr = await self._probe_step(4, emit, lang)
        await finish({"ok": bool(pr.get("ok")), "step": 4,
                      "text": rescue_repair.probe_line(pr, lang),
                      **({} if pr.get("ok") else {"why": pr.get("why") or "no_reply",
                                                 "rollback": bool(previous_wheel(self.h.st))})})
        return False

    async def _step4(self, device, finish, emit, steps, lang) -> bool:
        ok, res = await self._new_chat(device, 4, emit, lang, label=_t(lang, REPAIR_RUN[4]))
        if ok:
            steps.append((4, True))
            return await self._repaired(finish, steps, 4, lang, old=res.get("old_session"))
        steps.append((4, False))
        self.h.st.log("rescue_repair", result="all_failed")
        await finish({"ok": False, "step": 4, "why": "all_failed", "report": True,
                      "text": _summary(steps, lang) + "\n" + _t(lang, ALL_FAIL)})
        return False


    async def _repaired(self, finish, steps, n, lang, old=None) -> bool:
        self.h.st.log("rescue_repair", result="ok", step=n)
        await finish({"ok": True, "step": n, "steps": [x[0] for x in steps if x[1]],
                      "text": _summary(steps, lang),
                      **({"old_session": old} if old else {})})
        return False


    async def _probe_ok(self, step, emit, lang) -> bool:
        return bool((await self._probe_step(step, emit, lang)).get("ok"))


    async def _probe_step(self, step, emit, lang) -> dict:
        await emit(step, "run", _t(lang, rescue_repair.PROBE_RUN))
        pr = await rescue_repair.probe(self.h)
        await emit(step, "ok" if pr.get("ok") else "fail", rescue_repair.probe_line(pr, lang), pr.get("why"))
        return pr


    @staticmethod
    def _next_for(pr: dict, nxt: str) -> dict:
        if pr.get("why") == "estop":
            return {"resume": True, "cont": "probe"}
        return {"next": nxt} if nxt else {}


    async def _new_chat(self, device, step, emit, lang, finish_it=None, label=None):
        """「换个新对话」: stop what is stuck first, put the conversation aside (kept, reset_undo.json), prove the new one answers."""
        h = self.h
        await emit(step, "run", label or _t(lang, REPAIR_RUN[4])[2:])
        a = h.agent
        if a is not None and (getattr(a, "status", None) in ("working", "compacting") or _desktop_waiting(a)):
            sr = await rescue_repair.stop_hard(h, device)
            await emit(step, "ok" if sr.get("ok") else "fail", rescue_repair.stop_text(sr, lang), sr.get("why"))
        res = await new_session(h)
        if res.get("skip"):
            text, ok, why = _t(lang, NEW_SHARED), False, "shared"
            await emit(step, "skip", text)
        elif res.get("ok"):
            await emit(step, "ok", _t(lang, NEW_OK))
            pr = await self._probe_step(step, emit, lang)
            ok, why = bool(pr.get("ok")), pr.get("why")
            text = _t(lang, NEW_OK) + "\n" + rescue_repair.probe_line(pr, lang)
        else:
            why = res.get("why") or "failed"
            text, ok = _t(lang, NEW_FAIL), False
            await emit(step, "fail", text, why)
        if finish_it is not None:
            extra = {} if ok else ({"resume": True, "cont": "probe"} if why == "estop" else {"next": "restart_session" if why == "shared" else "repair"})
            await finish_it({"ok": ok, "step": step, "text": text, **({"old_session": res["old_session"]} if res.get("old_session") else {}),
                             **({} if ok else {"why": why or "failed"}), **extra})
        return ok, res


    async def _restart(self, device, r, flow, step, emit, finish, lang, cont, steps) -> bool:
        """「重启服务」: end the harness tree Agent J started (a detached backend would outlive the service otherwise — the
        customer's `agentj service restart` left Codex stuck), leave a marker, tell the page what to send once it is back
        (`cont`), then ask the service manager. → True when the restart was handed over (the page continues), False when it
        could not be (not a service / the manager refused): the caller goes on."""
        h = self.h
        from . import service as svc
        try:
            installed = bool((await asyncio.to_thread(svc.status)).get("installed"))
        except Exception:  # noqa: BLE001
            installed = False
        if not installed:
            await emit(step, "skip" if flow == "repair" else "fail", _t(lang, RESTART_NONE))
            if flow != "repair":
                await finish({"ok": False, "step": step, "why": "not_installed", "text": _t(lang, RESTART_NONE), "next": "restart_session"})
            return False
        await emit(step, "run", _t(lang, RESTART_RUN if flow != "repair" else REPAIR_RUN[3]))
        with contextlib.suppress(Exception):
            await rescue_repair.stop_hard(h, device)
        rescue_repair.write_marker(h.st, flow, r, device, steps=[list(x) for x in steps])
        await finish({"ok": True, "step": step, "pending": True, "cont": cont, "text": _t(lang, RESTART_WAIT),
                      **({"resume": True} if h.stopped() else {})})
        await asyncio.sleep(1.0)                  # let reset_done leave first
        res = await asyncio.to_thread(run_restart_serve, h.st)
        if res.get("ok"):
            return True
        with contextlib.suppress(OSError):
            (h.st.root / rescue_repair.MARKER).unlink()
        await emit(step, "fail", _t(lang, RESTART_FAIL), res.get("why") or res.get("detail"))
        if flow != "repair":
            await finish({"ok": False, "step": step, "why": "restart_failed", "text": _t(lang, RESTART_FAIL), "next": "help"})
        return False


# ------------------------------------------------------------------ serve glue
class Desk(_Rescue585):
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

        un = {"ran": False}

        async def finish_told(res):
            """#484: every reset / reopen result ends with what the /stop step did and what no button can fix."""
            text, why = await after_text(h, un, res.get("text") or "", lang)
            res = {**res, "text": text}
            if why and why != "noack":
                res.update(ok=False, why=why)
            await finish(res)

        ops = HostOps(h, device)
        try:
            if flow in P585_FLOWS:                       # #585: every button proves its promise (rescue_repair)
                restart_after = await self._p585(device, r, flow, emit, finish, ops, lang)
            elif flow == "restart_session":
                from . import phone_restart
                a0 = h.agent
                release = False
                if a0 is not None and _desktop_waiting(a0):
                    release = bool(await desktop_dead(a0))
                    un = {"ran": True, "ok": True, "how": "released"} if release else {"ran": True, "ok": False, "why": "desktop_live"}
                elif a0 is not None and getattr(a0, "status", None) in ("working", "compacting"):
                    un = {"ran": True, "ok": True, "how": "stopped"}      # the reopen ends that process: a /stop and more
                await emit(1, "run", pick(lang, "正在重启 Agent…", "Reopening the AI session…"))
                res = await phone_restart.run(h, release=release)
                if not res.get("ok") and un.get("ran") and un.get("ok"):
                    un = {"ran": False}
                text = pick(lang, "Agent 已重启，可以继续聊天，不需要到 Agent J 主机前。", "The Agent has restarted. Continue chatting without going to the computer.") if res.get("ok") else pick(lang, "没能重启 Agent，请试「请 Agent J Father 远程帮忙」。", "Could not restart the Agent. Try Ask Agent J Father for remote help.")
                if res.get("new"):
                    text = pick(lang, "原会话没能恢复，已开新会话并接好手机；之前的对话保存着，不需要到 Agent J 主机前。", "The old session could not resume. A new session is connected to your phone; the old conversation is kept. No need to go to the computer.")
                if res.get("ok"):          # #462: a pending Claude sign-in hand-over is proven in the reopened process
                    from . import claude_live
                    live = await claude_live.after_reopen(h)
                    text += ("\n" + live) if live else ""
                await emit(1, "ok" if res.get("ok") else "fail", text)
                extra = {}
                if res.get("ok"):          # #585: 「重启 Agent」 is done only when the new process answers one line
                    pr = await self._probe_step(2, emit, lang)
                    if not pr.get("ok"):
                        res = {**res, "ok": False, "why": pr.get("why") or "no_reply"}
                        text += "\n" + rescue_repair.probe_line(pr, lang)
                        extra = self._next_for(pr, "repair")
                    else:
                        text += "\n" + rescue_repair.probe_line(pr, lang)
                elif res.get("why") == "estop":
                    text = rescue_repair.probe_line({"why": "estop"}, lang)
                    extra = {"resume": True, "cont": "probe"}
                else:
                    extra = {"next": "restart"}
                await finish_told({**res, "step": 1, "text": text, **extra})
            elif flow in ("end_turn", "takeover"):
                await self._heal(device, flow, emit, finish, ops, lang)
            elif flow in ("reset", "reconnect"):
                if flow in ("reset", "new_session"):         # #484: what /stop does, first — never wait behind the turn
                    a0 = h.agent
                    if a0 is not None and (_desktop_waiting(a0) or getattr(a0, "status", None) in ("working", "compacting")):
                        await emit(1, "run", _t(lang, UNSTICK["run"]))
                        un = await unstick(h, device)
                        line = HEAL["foreign"] if un.get("why") == "foreign" else UNSTICK[unstick_key(un)]
                        await emit(1, "ok" if un.get("ok") else "fail", _t(lang, line), un.get("why"))
                steps = {"reset": (1, 2, 3, 4), "reconnect": (1, 2), "new_session": (3,), "restart": (4,)}[flow]
                if flow == "reset" and un.get("ran") and not un.get("ok"):
                    steps = (1, 2)      # a turn that would not stop: a new conversation / restart would not fix it either
                await ladder(ops, emit, finish_told if flow in ("reset", "new_session") else finish, lang, steps)
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

    async def _heal(self, device, flow, emit, finish, ops, lang) -> None:
        """0269 (#461): 「强制结束这一轮」 (end_turn) · 「接管卡住的会话」 (takeover): turn_heal.heal, then (takeover) the relay
        link back if it is down and the same conversation re-pinned."""
        h = self.h
        await emit(1, "run", pick(lang, *HEAL[flow][0]))
        res = await h.turn_heal.heal(device or "owner", None, action=flow)
        if flow == "takeover" and res.get("ok"):
            if not h.relay_up:
                await ops.reconnect()
            with contextlib.suppress(Exception):
                await ops.repin()
        why = res.get("why")
        text = pick(lang, *(HEAL[flow][1] if res.get("ok") else HEAL["estop" if why == "estop" else "foreign" if why == "foreign" else "fail"]))
        await emit(1, "ok" if res.get("ok") else "fail", text, why)
        extra = {}
        if flow == "takeover":     # #585: never a silent failure — the real reason, the next step, and proof it answers
            if res.get("ok"):
                pr = await self._probe_step(2, emit, lang)
                text += "\n" + rescue_repair.probe_line(pr, lang)
                if not pr.get("ok"):
                    res, why = {**res, "ok": False}, pr.get("why") or "no_reply"
                    extra = self._next_for(pr, "restart_session")
            else:
                text = _t(lang, TAKE_WHY.get(why) or TAKE_WHY["not_recovered"])
                extra = {"resume": True, "cont": "takeover"} if why == "estop" else {"next": TAKE_NEXT.get(why, "restart_session")}
        await finish({"ok": bool(res.get("ok")), "step": 1, "text": text, **({"why": why} if why else {}), **extra})

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
