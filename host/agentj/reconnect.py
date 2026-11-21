"""P147 reconnect: check serve, relay, then agent; repair only the failed link.
Report each step. Never print secrets; serve owns any exact-session recovery.
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
import time

from . import names

OK_AGENT = ("idle", "working", "waiting", "compacting")


def _say(lang, zh, en):
    return en if lang == "en" else zh


def _status(st, ctl):
    try:
        return ctl(st, {"cmd": "status"}, 5)
    except names.ServeBusy as e:
        return "busy" if e.response()["error"] == "serve_busy" else e.response()["error"]


def _wait(st, ctl, sleep, seconds, good):
    res = _status(st, ctl)
    for _ in range(seconds):          # one check per second
        if good(res):
            break
        sleep(1)
        res = _status(st, ctl)
    return res


def run(st, lang="zh", session=None, ask=None, out=None, ctl=None, service=None, sleep=time.sleep, env=None,
        as_json=False) -> int:
    out = out or sys.stdout
    ctl = ctl or names.ctl_call
    env = os.environ if env is None else env
    if service is None:
        from . import service
    steps, restarted = [], False
    up = lambda r: isinstance(r, dict)   # noqa: E731

    def finish(ok, zh, en, **extra):
        text = _say(lang, zh, en)
        if as_json:
            print(json.dumps({"ok": ok, "result": text, "steps": steps, **extra}, ensure_ascii=False), file=out)
        else:
            for s in steps:
                print("· " + s, file=out)
            print(("✓ " if ok else "✗ ") + text, file=out)
        return 0 if ok else 1

    # ---- 1. serve
    res = _status(st, ctl)
    if isinstance(res, str) and res in ("sandbox_blocked", "host_unreachable"):
        failure = names.transport_failure(res)
        return finish(False, failure["detail"], failure["detail"], error=res)
    if not up(res):
        try:
            svc = service.status()
            if not svc.get("installed"):
                return finish(False, "Agent J 的后台服务没装：运行 agentj service install，装好后再运行一次 agentj doctor --reconnect。",
                              "Agent J's background service is not installed: run agentj service install, then agentj doctor "
                              "--reconnect again.", broken="serve")
            if res == "busy" or svc.get("active") == "active":
                service.restart()
                steps.append(_say(lang, "后台服务没响应 → 已重启", "background service not answering → restarted"))
            else:
                service.start(st)
                steps.append(_say(lang, "后台服务没在运行 → 已启动", "background service was not running → started"))
            restarted = True
        except Exception as e:  # noqa: BLE001; report service errors
            return finish(False, f"后台服务起不来（{type(e).__name__}）：运行 agentj doctor 看具体原因。",
                          f"The background service would not start ({type(e).__name__}): run agentj doctor for the reason.",
                          broken="serve")
        res = _wait(st, ctl, sleep, 30, up)
        if not up(res):
            return finish(False, "后台服务启动后仍不响应：运行 agentj doctor 看具体原因。",
                          "The background service still does not answer after starting: run agentj doctor for the reason.",
                          broken="serve")

    # ---- 2. relay
    if res.get("relay_state", "up" if res.get("relay_up") else "reconnecting") != "up":
        res = _wait(st, ctl, sleep, 20, lambda r: up(r) and r.get("relay_state") == "up")
        if up(res) and res.get("relay_state") != "up" and not restarted:
            try:
                service.restart()
                restarted = True
                steps.append(_say(lang, "中继连不上 → 已重启后台服务", "relay not connected → background service restarted"))
            except Exception as e:  # noqa: BLE001; record a failed restart
                steps.append(_say(lang, f"中继连不上 → 重启后台服务失败（{type(e).__name__}）",
                                  f"relay not connected → restarting the background service failed ({type(e).__name__})"))
            res = _wait(st, ctl, sleep, 30, lambda r: up(r) and r.get("relay_state") == "up")
        if not up(res) or res.get("relay_state") != "up":
            from . import relaydiag     # Report observed host phase/reason/close.
            last = res.get("relay_last") if isinstance(res, dict) else None
            return finish(False, "Agent J 主机连不上 Agent J 中继，手机暂时联系不到它" + (f"：卡在 {relaydiag.text(last, 'zh')}" if last else
                          "（网络或账户问题）") + "。运行 agentj doctor 看主机腿探测。",
                          "the Agent J host cannot reach the Agent J relay, so the phone cannot reach it" + (
                          f": stuck at {relaydiag.text(last, 'en')}" if last else " (network or account)")
                          + ". Run agentj doctor for the host-leg probe.", broken="relay", relay_last=last)
        steps.append(_say(lang, "中继已连上", "relay connected"))

    # ---- 3. agent
    status = res.get("agent_status")
    if status == "stopped":
        return finish(False, "Agent J 处于急停：在已配对手机上点「恢复」，或主人在终端运行 agentj resume。",
                      "Agent J is emergency-stopped: tap Resume on a paired phone, or the owner runs agentj resume.",
                      broken="estop")
    if status in (None, "none"):
        return finish(False, "还没选主 Agent：运行 agentj agent claude（或 codex / opencode）。",
                      "No main Agent chosen yet: run agentj agent claude (or codex / opencode).", broken="agent")
    claude = res.get("agent") == "claude"
    shared = res.get("claude_shared") if claude else None
    caller = env.get("CLAUDE_CODE_SESSION_ID") if claude else None
    from_claude = claude and bool(caller or env.get("CLAUDECODE"))
    if shared is False and (session or from_claude):
        return finish(False, "后台服务和中继正常。手机当前连的是 Agent J 的 Claude 独立会话。要接这个终端，请先在手机设置中切到共享会话模式。",
                      "The service and relay are up. Your phone is on Agent J's independent Claude conversation. Switch to Shared session in phone Settings to connect this terminal.",
                      broken="agent", why="independent")
    if status not in OK_AGENT or session or shared or from_claude:
        if shared and not session:
            request = {"cmd": "agent_reconnect", "match_caller": True}
            if from_claude:
                request["require_caller"] = True
        else:
            request = {"cmd": "agent_reconnect", "session": session or caller}
        r = ctl(st, request, 45) or {}
        if (shared or session or caller) and r.get("ok") and not r.get("session"):
            r = {"ok": False, "why": "target_unavailable"}
        if not r.get("ok") and r.get("why") == "ambiguous":
            choices = r.get("choices") or []
            ask = (sys.stdin.isatty() and sys.stdout.isatty()) if ask is None else ask
            if ask and choices:
                for i, c in enumerate(choices, 1):
                    print(f"  {i}. {c}", file=out)
                answer = input(_say(lang, f"接哪一个？[1-{len(choices)}] ", f"Which one? [1-{len(choices)}] ")).strip()
                if answer.isdigit() and 1 <= int(answer) <= len(choices):
                    r = ctl(st, {"cmd": "agent_reconnect", "session": choices[int(answer) - 1]}, 45) or {}
            if not r.get("ok"):
                ids = ", ".join(choices)
                return finish(False, f"这个目录开着好几个会话（{ids}），不知道接哪个：在要用的那个会话里运行 agentj doctor --reconnect，"
                                     "或运行 agentj doctor --reconnect --session <会话 ID>。",
                              f"Several sessions are open in the folder ({ids}): run agentj doctor --reconnect inside the one "
                              "you want, or agentj doctor --reconnect --session <session ID>.", broken="agent", choices=choices)
        if not r.get("ok") and r.get("why") == "unsupported" and not (session or from_claude or restarted):
            service.restart()
            steps.append(_say(lang, "Agent J 没起来 → 已重启后台服务", "Agent J down → background service restarted"))
            res2 = _wait(st, ctl, sleep, 30, lambda x: up(x) and x.get("agent_status") in OK_AGENT)
            r = {"ok": up(res2) and res2.get("agent_status") in OK_AGENT}
        why = r.get("why")
        if not r.get("ok") and (session or from_claude or why in ("caller_unverified", "busy", "target_unavailable")):
            sid = r.get("session")
            current_zh = f"手机当前连的是 Claude 会话 {sid}。" if sid else "尚未确认手机当前接到哪个会话。"
            current_en = f"Your phone is currently on Claude session {sid}." if sid else "The phone's current session could not be verified."
            if why == "busy":
                fix_zh, fix_en = "等当前手机消息处理完再重试。", "Retry after the current phone turn finishes."
            else:
                fix_zh = "请在所需 Claude 会话的工作目录里重试，或指定 agentj doctor --reconnect --session <会话 ID>。"
                fix_en = "Retry in the intended Claude session's working folder or run agentj doctor --reconnect --session <session ID>."
            return finish(False, f"没有接到指定的终端会话。{current_zh}{fix_zh}",
                          f"The requested terminal session was not attached. {current_en} {fix_en}", broken="agent", why=why, session=sid)
        if not r.get("ok"):
            return finish(False, "Agent J 还是接不上：Agent J 主机上这个目录里没有开着的会话。在工作目录里打开 Claude Code（或 Codex / OpenCode），"
                                 "它会自动接上；不行就运行 agentj doctor 并按 agentj-manual 的求助步骤。",
                          "Agent J still is not attached: no session is open in its folder. Open Claude Code (or Codex / "
                          "OpenCode) in the working folder and it attaches by itself; otherwise run agentj doctor and follow "
                          "the agentj-manual support steps.", broken="agent", why=r.get("why"))
        if shared and r.get("session"):
            sid = r["session"]
            if r.get("current_terminal"):
                return finish(True, f"手机已接到这个终端里的 Claude 会话 {sid}。", f"Your phone is attached to Claude session {sid} in this terminal.", session=sid)
            if session:
                return finish(True, f"手机已接到指定的 Claude 会话 {sid}。", f"Your phone is attached to the selected Claude session {sid}.", session=sid)
            return finish(True, f"后台服务和中继正常。手机当前连的是 Claude 会话 {sid}。", f"The service and relay are up. Your phone is currently on Claude session {sid}.", session=sid)
        if r.get("session"):
            steps.append(_say(lang, ("已接上会话 " if r.get("changed") else "会话正常 ") + r["session"],
                              ("attached session " if r.get("changed") else "session fine ") + r["session"]))
    else:
        with contextlib.suppress(Exception):   # Refresh stale phone status.
            ctl(st, {"cmd": "status_push"}, 5)
    if shared is False:
        return finish(True, "后台服务和中继正常。手机当前连的是 Agent J 的 Claude 独立会话。",
                      "The service and relay are up. Your phone is on Agent J's independent Claude conversation.")
    return finish(True, "三段都通：后台服务在跑、中继已连上、Agent J 已接上。手机上下拉刷新或等几秒就会变回正常颜色。",
                  "All three links are up: the service runs, the relay is connected and Agent J is attached. The phone "
                  "turns back to its normal colour within seconds (or pull to refresh).")
