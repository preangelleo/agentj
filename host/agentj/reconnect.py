"""P147 `agentj doctor --reconnect`: one command that brings a grey phone back.

Three links, checked in order, each repaired only when it is the broken one:
  serve  — the background service (`agentj service`); not answering → start / restart it
  relay  — serve ↔ the Agent J relay (the phone's line); not up → wait, then one service restart
  agent  — serve ↔ the AI session it serves; `down` → re-attach (shared Claude: follow the owner to the live session;
           run inside a Claude Code session, that session is the one attached)
Prints one plain sentence (and the steps taken). Never prints a secret; never starts a Claude Code process itself.
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
    except names.ServeBusy:
        return "busy"


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
        except Exception as e:  # noqa: BLE001 — a service manager error is reported, never raised at the owner
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
            except Exception as e:  # noqa: BLE001 — P192 (B18): the step list says the restart did not happen
                steps.append(_say(lang, f"中继连不上 → 重启后台服务失败（{type(e).__name__}）",
                                  f"relay not connected → restarting the background service failed ({type(e).__name__})"))
            res = _wait(st, ctl, sleep, 30, lambda r: up(r) and r.get("relay_state") == "up")
        if not up(res) or res.get("relay_state") != "up":
            from . import relaydiag     # P317: where the host leg sticks (phase / reason / close code), not a guess
            last = res.get("relay_last") if isinstance(res, dict) else None
            return finish(False, "电脑连不上 Agent J 中继，手机暂时联系不到它" + (f"：卡在 {relaydiag.text(last, 'zh')}" if last else
                          "（网络或账户问题）") + "。运行 agentj doctor 看主机腿探测。",
                          "This computer cannot reach the Agent J relay, so the phone cannot reach it" + (
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
    here = session or (env.get("CLAUDE_CODE_SESSION_ID") if res.get("agent") == "claude" else None)
    if status not in OK_AGENT or session:
        r = ctl(st, {"cmd": "agent_reconnect", "session": here}, 25) or {}
        if not r.get("ok") and r.get("why") == "not_found" and here and not session:
            r = ctl(st, {"cmd": "agent_reconnect"}, 25) or {}      # this session is not in the Agent's folder
        if not r.get("ok") and r.get("why") == "ambiguous":
            choices = r.get("choices") or []
            ask = (sys.stdin.isatty() and sys.stdout.isatty()) if ask is None else ask
            if ask and choices:
                for i, c in enumerate(choices, 1):
                    print(f"  {i}. {c}", file=out)
                answer = input(_say(lang, f"接哪一个？[1-{len(choices)}] ", f"Which one? [1-{len(choices)}] ")).strip()
                if answer.isdigit() and 1 <= int(answer) <= len(choices):
                    r = ctl(st, {"cmd": "agent_reconnect", "session": choices[int(answer) - 1]}, 25) or {}
            if not r.get("ok"):
                ids = ", ".join(choices)
                return finish(False, f"这个目录开着好几个会话（{ids}），不知道接哪个：在要用的那个会话里运行 agentj doctor --reconnect，"
                                     "或运行 agentj doctor --reconnect --session <会话 ID>。",
                              f"Several sessions are open in the folder ({ids}): run agentj doctor --reconnect inside the one "
                              "you want, or agentj doctor --reconnect --session <session ID>.", broken="agent", choices=choices)
        if not r.get("ok") and r.get("why") == "unsupported" and not restarted:
            service.restart()
            steps.append(_say(lang, "Agent J 没起来 → 已重启后台服务", "Agent J down → background service restarted"))
            res2 = _wait(st, ctl, sleep, 30, lambda x: up(x) and x.get("agent_status") in OK_AGENT)
            r = {"ok": up(res2) and res2.get("agent_status") in OK_AGENT}
        if not r.get("ok"):
            return finish(False, "Agent J 还是接不上：电脑上这个目录里没有开着的会话。在工作目录里打开 Claude Code（或 Codex / OpenCode），"
                                 "它会自动接上；不行就运行 agentj doctor 并按 agentj-manual 的求助步骤。",
                          "Agent J still is not attached: no session is open in its folder. Open Claude Code (or Codex / "
                          "OpenCode) in the working folder and it attaches by itself; otherwise run agentj doctor and follow "
                          "the agentj-manual support steps.", broken="agent", why=r.get("why"))
        if r.get("session"):
            steps.append(_say(lang, ("已接上会话 " if r.get("changed") else "会话正常 ") + r["session"],
                              ("attached session " if r.get("changed") else "session fine ") + r["session"]))
    else:
        with contextlib.suppress(Exception):   # P306: links fine, phone may still show an old status → re-send it
            ctl(st, {"cmd": "status_push"}, 5)
    return finish(True, "三段都通：后台服务在跑、中继已连上、Agent J 已接上。手机上下拉刷新或等几秒就会变回正常颜色。",
                  "All three links are up: the service runs, the relay is connected and Agent J is attached. The phone "
                  "turns back to its normal colour within seconds (or pull to refresh).")
