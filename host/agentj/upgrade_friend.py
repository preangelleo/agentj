"""R-0060: an upgrade a friend asks for — a Same owner device included — never runs on the friend's authority.

A consistency gate, not a security boundary: it makes every harness behave the same for a friend's request. Same-uid code
can always reach the installer by other means; the boundaries stay the fence (read-only install tree) and the signed-wheel
verification. ADR-R0060 lists what this cannot see.

- CLI (`guard`, called by every mutating `agentj update` entrypoint: apply incl. --yes / --reinstall / --wheel /
  --authorization, rollback): asks the host whether the running turn is a friend's. Friend → `reason: friend_request`
  (nothing done, ONE owner card + notice). An Agent caller (harness env marker / AGENTJ_PERM_*) whose host answer cannot
  be verified (no socket, timeout, error, malformed answer, fence socket failure) → `reason: host_unverified` (fail
  closed, same /update hint). A caller without an Agent marker and no host answer: unchanged (the owner at a terminal).
- serve (`origin`, host-held facts only — the running Send's source and the caller's process ancestry; turn text and
  request fields never read): control.sock `upgrade_friend`, elevate.sock op `friend`; and the fenced host ops
  pending / service / prepare / rollback / marker / clear refuse during a friend turn (`host_op_refusal`).
- Card: P203 `upg_card`, created under one lock; ANY live upgrade card is reused; one card + notice per friend per
  RATE seconds. Its 「现在升级」 is that phone's own signed /update (serve.phone_upgrade → the normal signed update job).
"""
from __future__ import annotations

import asyncio
import os
import time

# Turns carrying third-party data into the main session (a friend, a handed-over friend message, a WeChat request, an
# official remote-assist task). scheduled_task is left out on purpose: it is the owner's own automation.
KINDS = ("same_owner", "friend_task", "wechat_task", "official_assist")
RATE = 600                      # s: one card + notice per friend
MUTATING_OPS = ("pending", "service", "prepare", "rollback", "marker", "clear")


def _agent_tree(host, pid) -> bool | None:
    """True = the peer process descends from serve or from the served Agent session (kernel ancestry); False = it does not;
    None = the peer is unknown / unreadable. Harness env markers are deliberately not consulted: the owner's own separate
    Claude / Codex session carries them too."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return None
    try:
        from . import ctl_auth
        chain = ctl_auth.ancestors(pid)
        if not chain:
            return None
        me = os.getpid()
        roots = {p for p in host._agent_pids() if isinstance(p, int) and p > 1}
        return (pid != me and me in chain[1:]) or bool(set(chain) & roots)
    except Exception:  # noqa: BLE001
        return None


def origin(host, pid=None, caller=None) -> dict | None:
    """{"kind", "fid"} when the main Agent's running turn came from a friend and the caller is that Agent, else None.
    caller="fenced": the elevate.sock peer is the fenced Agent by construction. Otherwise the caller is decided by process
    ancestry when the peer pid is known; only an unknown peer falls back to the CLI's harness-marker claim."""
    agent = getattr(host, "agent", None)
    send = getattr(agent, "cur_send", None) if agent is not None else None
    src = getattr(send, "source", None)
    if not (isinstance(src, dict) and src.get("kind") in KINDS):
        return None
    if caller != "fenced":
        tree = _agent_tree(host, pid)
        if not (tree if tree is not None else caller == "agent"):
            return None                 # the owner's terminal / own separate session while a friend turn runs
    fid = getattr(send, "fid", None)
    return {"kind": src["kind"], "fid": fid if isinstance(fid, str) else ""}


def host_op_refusal(host, req: dict) -> dict | None:
    """elevate.sock upgrade ops that install / restart / roll back: refused while a friend's turn runs (fenced caller)."""
    if req.get("op") in MUTATING_OPS and origin(host, caller="fenced") is not None:
        host.st.log("upgrade_host", op=str(req.get("op")), result="refused", reason="friend_request")
        return {"ok": False, "why": "friend_request"}
    return None


def _name(host, fid: str) -> str:
    """Display name only (never the AJ-… id); unnamed / unreadable → ""."""
    try:
        from .plaza import display_name
        f = host.peers.store.get(fid) or {}
        card = f.get("card") if isinstance(f.get("card"), dict) else {}
        n = display_name(card.get("name"))
        return "" if n == fid else n
    except Exception:  # noqa: BLE001
        return ""


def known_latest(host) -> str:
    """Only the host's own daily-check record, and only when it is newer; never a version the Agent supplied."""
    from . import auto_update, update
    latest = update.read_rec(host.st).get("latest")
    return latest[:32] if isinstance(latest, str) and auto_update.newer_known(host.st) else ""


def notice(name: str, to: str, kind: str, fenced: bool, lang: str) -> str:
    """The owner's chat line about the card."""
    if lang == "en":
        who = {"wechat_task": "A WeChat message", "official_assist": "An official remote-assist task"}.get(kind) or \
            ((f'Your other device "{name}"' if name else "Your other device") if kind == "same_owner" else
             (f'Friend "{name}"' if name else "A friend"))
        text = (f"{who} asked this computer to upgrade{' to ' + to if to else ''}. Nothing was installed: someone else's request "
                "never upgrades on its own. Tap Upgrade now on the card to upgrade, Not now to leave it.")
        return text + (" The Agent runs isolated here and cannot upgrade itself; " if fenced else " ") + \
            "After the card is gone, send /update in the remote."
    who = {"wechat_task": "一条微信消息", "official_assist": "一个官方远程协助任务"}.get(kind) or \
        ((f"你的另一台设备「{name}」" if name else "你的另一台设备") if kind == "same_owner" else
         (f"好友「{name}」" if name else "一位好友"))
    text = f"{who}请这台 Agent J 主机升级{'到 ' + to if to else ''}。还没有升级：好友的请求不会自己触发升级。点卡片上「现在升级」就升级，点「先不升」就不升。"
    return text + ("这台 Agent J 主机的 Agent 在隔离里运行，不能自己升级；" if fenced else "") + "卡片关掉后，也可以随时在遥控器发 /update。"


def reply_line(cur: str, to: str, fenced: bool, recent: bool = False) -> str:
    """The fixed status line the Agent sends back to the friend: versions and state only, nothing private.
    recent: no card now (the owner was asked within RATE s and the card is gone) — the owner can send /update."""
    if recent:
        zh = (f"这台 Agent J 主机现在是 {cur}，最新版是 {to or '?'}。好友的请求不能代替主人授权升级；主人最近已经被问过，这次没有再发确认卡，"
              "主人可以随时在遥控器发 /update。")
        en = (f"This computer runs {cur}; the latest is {to or '?'}. A friend cannot authorise an upgrade; the owner was asked "
              "recently, so no new card was sent — the owner can send /update in the remote at any time.")
        return f"{zh} / {en}"
    zh = f"这台 Agent J 主机现在是 {cur}，最新版是 {to or '?'}。好友的请求不能代替主人授权升级，已请主人在遥控器确认。"
    en = f"This computer runs {cur}; the latest is {to or '?'}. A friend cannot authorise an upgrade: waiting for the owner to confirm on the remote."
    if fenced:
        zh += "这里的 Agent 在隔离里不能自己升级，主人在遥控器发 /update 即可。"
        en += " The Agent here runs isolated and cannot upgrade itself; the owner sends /update in the remote."
    return f"{zh} / {en}"


async def open_card(host, who: dict, fenced: bool = False) -> dict:
    """At most ONE upgrade card: a live card (the owner's own P203 card too) is reused, never replaced; one card + notice
    per friend per RATE s. Serialised: concurrent requests wait for the first and then see its card."""
    lock = host.__dict__.get("_upg_friend_lock")
    if lock is None:
        lock = host.__dict__["_upg_friend_lock"] = asyncio.Lock()
    async with lock:
        to = known_latest(host)
        key = (who["kind"], who["fid"])
        seen = host.__dict__.setdefault("_upg_friend_seen", {})
        now = time.monotonic()
        for k in [k for k, t in seen.items() if now - t >= RATE]:
            seen.pop(k)
        live = getattr(host, "upg", None) is not None and host._upg_live()
        if live or key in seen:
            host.st.log("upgrade_friend", kind=who["kind"], fenced=fenced, result="reused" if live else "rate_limited")
            return {"id": host.upg["id"] if live else None, "friend": who["kind"], "to": to, "card": "reused" if live else "rate_limited"}
        name = await asyncio.to_thread(_name, host, who["fid"]) if who["fid"] else ""
        if getattr(host, "upg", None) is not None and host._upg_live():      # recheck after the await
            return {"id": host.upg["id"], "friend": who["kind"], "to": to, "card": "reused"}
        seen[key] = now
        while len(seen) > 64:
            seen.pop(next(iter(seen)))
        res = host.upg_open("agent")
        host.upg["friend"] = key
        if to and not host.upg.get("to"):
            host.upg["to"] = to
        host.st.log("upgrade_friend", id=res["id"], kind=who["kind"], fenced=fenced, phones=res.get("phones"), result="card")
        lang = "en" if getattr(host, "lang", "zh") == "en" else "zh"
        host.agent_notice(notice(name, to, who["kind"], fenced, lang))
        return {"id": res["id"], "friend": who["kind"], "to": to, "card": "new"}


async def answer(host, pid=None, caller=None, fenced=False) -> dict:
    """control.sock `upgrade_friend` / elevate.sock op `friend`: always `ok` + an explicit `friend` (None = not a friend)."""
    who = origin(host, pid, "fenced" if fenced else caller)
    if who is None:
        return {"ok": True, "friend": None}
    res = await open_card(host, who, fenced=fenced)
    return {"ok": True, "friend": res["friend"], "to": res["to"], "card": res["card"]}


# ------------------------------------------------------------------ CLI side
def agent_caller(environ=None) -> bool:
    from . import ctl_auth
    return bool(set(os.environ if environ is None else environ) & set(ctl_auth.AGENT_ENV))


def _refused(reason: str, to: str, fenced: bool, card: str | None = None) -> dict:
    from . import update
    recent = card == "rate_limited"
    return {"result": "refused", "reason": reason, "from": update.__version__, "to": to or None, "service": "unchanged",
            "reply_friend": reply_line(update.__version__, to, fenced, recent) if reason == "friend_request" else None,
            **({"card": "rate_limited"} if recent and reason == "friend_request" else {}),
            "exit": update._exit_of(reason)}


def guard(st, action: str) -> dict | None:
    """The one check every mutating `agentj update` entrypoint runs first. None = go on; else the refusal to print."""
    from . import fence, update
    from .names import ctl_call
    agent = agent_caller()
    try:
        fenced = fence.inside()
    except Exception:  # noqa: BLE001
        fenced = agent
    if fenced:
        ans = update._host(st, "friend", 15, action=action)
    else:
        try:
            ans = ctl_call(st, {"cmd": "upgrade_friend", "caller": "agent" if agent else None, "action": action}, 10)
        except Exception:  # noqa: BLE001 — busy / broken socket: unverified
            ans = False
    ok = isinstance(ans, dict) and ans.get("ok") is True and "friend" in ans and (ans["friend"] is None or ans["friend"] in KINDS)
    if ok and ans["friend"] in KINDS:
        to = ans.get("to") if isinstance(ans.get("to"), str) and update.parse(ans["to"]) is not None else ""
        return _refused("friend_request", to, fenced, ans.get("card"))
    if ok:
        return None
    if not agent and not fenced:
        return None                     # the owner at a terminal without a running host: unchanged
    return _refused("host_unverified", "", fenced)
