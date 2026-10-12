"""P155 (ADR-P155): `/switch-harness` from the phone menu + `agentj agent switch <claude|codex|opencode>`.

The menu item only inserts `/switch-harness ` (the owner may add words, then sends). serve never runs it: the message goes to
the CURRENT main Agent rewritten by `agent_text` (no harness takes it for one of its own slash commands), and the Agent follows
the bundled skill agentj-switch-harness — explain, scan (`agentj agent detect --probe --json`), report, guide installs/logins.

The switch itself is guarded, because the Agent that asks is the one being replaced and a dead harness on the other side
leaves a phone-only owner with nobody to talk to:
  1. the caller checks the target / model / that serve runs as a service, then starts a one-shot job outside Agent J's service
     (service_recovery.launch_job, the upgrade worker's way out of the service cgroup and the fence) and waits for its verdict.
  2. the job runs the gate in the SERVICE's environment (the one the new harness will get): the target is installed AND
     answers one tool-free headless call with the owner's own login (harness.probe, ≤ 60 s). Not callable → receipt
     `failed`, nothing changes (the caller exits 3). Passed → it waits until serve is idle, writes the new agent kind (folder,
     isolation and docker unchanged), restarts the service and verifies: serve answers with the new kind, its agent_status
     is up, the new Agent answers the host's 「自报家门」 turn and is idle again.
  3. any failure → the previous agent config is written back byte-for-byte, the service restarted, and the previous Agent is
     told why (a host turn; a phone notice if it cannot answer). Receipt `rolled_back`.
Receipt: harness-switch.json in the state dir + a copy beside the service definition (readable from inside the fence); fixed
codes only — never native output, never credentials.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

from . import harness, service
from .compose import Send

KINDS = ("claude", "codex", "opencode")
LABEL = harness.LABEL
CMD = "switch-harness"
RECEIPT = "harness-switch.json"
READY_LIMIT = 300     # s the job waits for the current turn (the one that asked) to finish
UP_WAIT = 120         # s for serve + the new Agent to come up after the restart
HELLO_WAIT = 180      # s for the new Agent's self-introduction
IDLE_WAIT = 120       # s for it to be idle again afterwards
POLL = 2.0
_CODE = re.compile(r"[a-z_]{1,40}")
_OC_MODEL = re.compile(r"[A-Za-z0-9._-]+/[^\s/][^\s]*")

REASON = {
    "zh": {"not_installed": "{x} 没有安装", "not_supported": "{x} 暂不支持",
           "native_timeout": "{x} 60 秒内没有回答（多半是没登录、token 失效，或网络 / 代理不通）",
           "native_failed": "{x} 没能完成一次调用（多半是没登录或 token 失效）", "call_failed": "{x} 没能完成一次调用（多半是没登录或 token 失效）",
           "empty_probe_reply": "{x} 回了空话", "provider_unavailable": "{x} 配置的模型服务不可用",
           "invalid_native_config": "{x} 的配置文件有问题", "invalid_provider": "{x} 配置的模型服务地址不合规",
           "native_executable_missing": "找不到 {x} 的程序", "native_executable_not_executable": "{x} 的程序不能运行",
           "native_executable_selection_failed": "{x} 有多个安装、选不出该用哪一个", "native_spawn_failed": "{x} 启动不了",
           "native_output_limit": "{x} 输出异常", "harness_unavailable": "{x} 不可用",
           "same": "现在用的就是 {x}", "bad_target": "只能切到 Claude Code、Codex 或 OpenCode",
           "bad_model": "OpenCode 的模型要写成「服务商/模型」，例如 zhipuai/glm-5.3",
           "no_service": "Agent J 没有作为后台服务运行（切换要靠它自动重启；先运行 agentj service install）",
           "no_agent": "Agent J 还没接任何 Agent", "not_running": "Agent J 没在运行", "estop": "Agent J 正处于急停状态",
           "launch_failed": "安排不了切换任务", "restart_failed": "重启 Agent J 失败",
           "wrong_agent": "重启后接上的不是 {x}", "agent_down": "重启后 {x} 没有接上",
           "hello_refused": "重启后 {x} 不接收消息", "no_reply": "{x} 三分钟内没有自报家门", "not_idle": "{x} 回答后一直没有空下来"},
    "en": {"not_installed": "{x} is not installed", "not_supported": "{x} is not supported yet",
           "native_timeout": "{x} did not answer within 60 s (usually not signed in, an expired token, or no network / proxy)",
           "native_failed": "{x} could not complete a call (usually not signed in or an expired token)",
           "call_failed": "{x} could not complete a call (usually not signed in or an expired token)",
           "empty_probe_reply": "{x} replied with nothing", "provider_unavailable": "{x}'s configured model service is unavailable",
           "invalid_native_config": "{x}'s config file has a problem", "invalid_provider": "{x}'s model service address is not allowed",
           "native_executable_missing": "{x}'s program was not found", "native_executable_not_executable": "{x}'s program cannot run",
           "native_executable_selection_failed": "{x} is installed more than once and none could be chosen",
           "native_spawn_failed": "{x} could not start", "native_output_limit": "{x} produced abnormal output",
           "harness_unavailable": "{x} is unavailable", "same": "{x} is already the one in use",
           "bad_target": "only Claude Code, Codex or OpenCode", "bad_model": "an OpenCode model is written provider/model, e.g. zhipuai/glm-5.3",
           "no_service": "Agent J is not running as a background service (the switch needs it to restart by itself; run agentj service install first)",
           "no_agent": "Agent J has no Agent configured", "not_running": "Agent J is not running", "estop": "Agent J is emergency-stopped",
           "launch_failed": "the switch job could not be scheduled", "restart_failed": "restarting Agent J failed",
           "wrong_agent": "after the restart the Agent was not {x}", "agent_down": "after the restart {x} did not come up",
           "hello_refused": "after the restart {x} did not take messages", "no_reply": "{x} did not introduce itself within 3 minutes",
           "not_idle": "{x} did not become idle after answering"},
}


def reason_text(code: str, target: str, lang: str = "zh") -> str:
    t = REASON["en" if lang == "en" else "zh"]
    return t.get(code, t["call_failed"]).format(x=LABEL.get(target, target))


# ------------------------------------------------------------------ the phone's /switch-harness → the main Agent
def agent_text(text, lang: str = "zh") -> str | None:
    """The message the main Agent gets for `/switch-harness [words]` (None for anything else). Never starts with "/":
    Claude Code (desktop or headless) would take it for one of its own slash commands."""
    if not isinstance(text, str):
        return None
    m = re.match(r"^[/／]switch[-_]harness(?=\s|$)(.*)$", text.strip(), re.S | re.I)
    if not m:
        return None
    arg = m.group(1).strip()
    if lang == "en":
        head = ("[Menu command /switch-harness] The owner sent \"switch harness\" from the phone menu. Follow the bundled "
                "skill agentj-switch-harness (scan, probe headless, switch only through its gate).")
        tail = (" The owner added: " + arg) if arg else (
            " The owner added nothing: first explain in two or three plain sentences what a harness is, then scan this "
            "computer and say which ones it can switch to now; to switch, the owner replies \"switch to Claude Code / Codex / "
            "OpenCode\".")
        return head + tail
    head = "〔菜单命令 /switch-harness〕主人从手机菜单发来「切换 harness」。按随包技能 agentj-switch-harness 处理（先扫描、无头探测，过了闸门才切换）。"
    tail = ("主人附加的话：" + arg) if arg else (
        "主人没有附加说明：先用两三句大白话解释什么是 harness，再扫描这台电脑，告诉主人现在能切到哪些；"
        "要切换就回复「切换到 Claude Code / Codex / OpenCode」。")
    return head + tail


class HelloSend(Send):
    """The host's 「自报家门」 / 「切回来了」 turn: the Agent's ordinary queue, outside any phone page (turn 0, like the
    onboarding welcome), so the reply lands as the Agent's own message. Provenance: the owner's own host, no person."""

    def __init__(self, text: str):
        from .provenance import system
        super().__init__("harness-switch", "hello", 0, text=text, by="Agent J", source=system())


def hello_prompt(kind: str, req: dict, lang: str = "zh") -> str | None:
    """Fixed host text for serve's `harness_hello` (the job sends kinds and a code, never free text)."""
    outcome, frm, to, code = req.get("outcome"), req.get("from"), req.get("to"), req.get("reason") or ""
    if outcome not in ("ok", "rolled_back") or frm not in KINDS or to not in KINDS or not (code == "" or _CODE.fullmatch(code)):
        return None
    if (outcome == "ok" and kind != to) or (outcome == "rolled_back" and kind != frm):
        return None
    F, T = LABEL[frm], LABEL[to]
    if lang == "en":
        if outcome == "ok":
            return ("[Agent J system note — not from the owner] The owner just switched the underlying harness from " + F
                    + " to you (" + T + "). In one or two sentences introduce yourself to the owner: which harness you are and "
                    "which model you run (say if you are not sure), that the switch is done and the conversation can go on; the "
                    "earlier conversation stays with " + F + " — saying \"switch back to " + F + "\" returns to it. "
                    "Do not use tools and do not mention this note.")
        return ("[Agent J system note — not from the owner] The owner asked to switch to " + T + ", but the check after the "
                "switch failed (" + reason_text(code, to, "en") + "), so Agent J switched back to you (" + F + ") by itself. In "
                "one or two sentences tell the owner: the switch did not work, you are back on the original harness, the reason "
                "in plain words, and the next step (e.g. send /switch-harness again so you re-check). Do not use tools and do "
                "not mention this note.")
    if outcome == "ok":
        return ("〔Agent J 系统提示，不是主人发的〕主人刚把底层 harness 从 " + F + " 切换到了你（" + T + "）。用一两句话向主人自报家门："
                "你是哪个 harness、用的什么模型（不确定就说不确定），说明切换已完成、可以接着说话；之前的对话留在 " + F
                + " 那边，主人说「切回 " + F + "」就能回去。不要调用工具，不要提这条提示。")
    return ("〔Agent J 系统提示，不是主人发的〕主人刚才要求切换到 " + T + "，但切换后的自检没有通过（" + reason_text(code, to)
            + "），Agent J 已经自动切回你（" + F + "）。用一两句话告诉主人：切换没成功、已经回到原来的 harness、失败原因的大白话，"
            "以及下一步建议（比如再发一次 /switch-harness 让你重新检查）。不要调用工具，不要提这条提示。")


# ------------------------------------------------------------------ receipts (fixed codes only)
SCAN = "harness-scan.json"


def receipt_path(name: str = RECEIPT) -> Path:
    tail = "." + name    # agentj.harness-switch.json / agentj.harness-scan.json beside the unit / plist (fence-readable)
    if service.platform() == "macos":
        return Path(service.plist_path(service.name())).with_suffix(tail)
    return Path(service.unit_dir()) / (service.name() + tail)


def write_receipt(st, rec: dict, name: str = RECEIPT) -> dict:
    rec = {**rec, "at": time.time()}
    data = json.dumps(rec).encode()
    for p in (st.root / name, receipt_path(name)):    # the job writes both; a fenced caller can write neither, read the second
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            service._write(str(p), data, 0o600)
        except OSError:
            pass
    return rec


def read_receipt(st, name: str = RECEIPT) -> dict | None:
    recs = []
    for p in (st.root / name, receipt_path(name)):
        try:
            r = json.loads(p.read_text())
            if isinstance(r, dict) and isinstance(r.get("at"), (int, float)):
                recs.append(r)
        except (OSError, ValueError):
            pass
    return max(recs, key=lambda r: r["at"]) if recs else None


# ------------------------------------------------------------------ 1. the gate (in the caller)
def gate(target: str, model: str | None = None, probe=None) -> dict:
    """Installed AND one tool-free headless call answered with the owner's own login. {ok, reason?, version}."""
    d = (probe or harness.probe)(only=(target,), model=model)
    h = next((x for x in d.get("harnesses", []) if x.get("name") == target), None)
    if not h or not h.get("installed"):
        return {"ok": False, "reason": "not_installed", "version": None}
    if h.get("callable"):
        return {"ok": True, "version": h.get("version")}
    code = h.get("probe_reason") or "call_failed"
    return {"ok": False, "reason": code if _CODE.fullmatch(code) else "call_failed", "version": h.get("version")}


def current_kind(st) -> str | None:
    try:
        return (st.agent_config() or {}).get("kind")
    except Exception:  # noqa: BLE001 — inside the fence the state is hidden: the job checks again
        return None


PROBE_WAIT = 90       # s the caller waits for the job's verdict (its probe is bounded by harness.PROBE_TIMEOUT)


def request(st, target: str, model: str | None = None, dry_run: bool = False, *, launch=None,
            sleep=time.sleep, clock=time.monotonic) -> dict:
    """Pre-checks here; the gate itself runs in the job, because the job has the SERVICE's environment — the one the new
    harness will run with (a key that lives only in the service env is invisible to a fenced caller, and the caller's own
    shell may hold keys the service lacks). The caller waits for the verdict: failed (nothing changed) · probe_ok (dry run) ·
    waiting (passed; the switch follows when this turn ends)."""
    if target not in KINDS:
        return {"ok": False, "reason": "bad_target", "target": target}
    from .slash import MODEL_RE
    if model and not (_OC_MODEL.fullmatch(model) if target == "opencode" else MODEL_RE.fullmatch(model)):
        return {"ok": False, "reason": "bad_model", "target": target}
    cur = current_kind(st)
    if cur == target:
        return {"ok": False, "reason": "same", "target": target, "from": cur}
    job = os.urandom(8).hex()      # the service check is the job's: inside the fence the unit is invisible (QA-J 10-12)
    write_receipt(st, {"status": "probing", "target": target, "from": cur, "job": job})
    argv = service.agentj_argv() + ["agent", "switch", target, "--worker", "--job", job] + (["--model", model] if model else []) \
        + (["--dry-run"] if dry_run else [])
    try:
        _launch(st, argv, launch)
    except Exception:  # noqa: BLE001
        write_receipt(st, {"status": "failed", "target": target, "from": cur, "reason": "launch_failed", "job": job})
        return {"ok": False, "reason": "launch_failed", "target": target, "from": cur}
    end = clock() + PROBE_WAIT
    while True:
        rec = read_receipt(st) or {}
        if rec.get("job") == job and rec.get("status") != "probing":
            break
        if clock() >= end:
            return {"ok": False, "pending": True, "reason": "probe_pending", "target": target, "from": cur}
        sleep(1.0)
    base = {"target": target, "from": rec.get("from") or cur, "version": rec.get("version")}
    if rec["status"] == "probe_ok":
        return {"ok": True, "dry_run": True, **base}
    if rec["status"] == "failed":
        return {"ok": False, "reason": rec.get("reason") or "call_failed", **base}
    return {"ok": True, "scheduled": True, **base}


SCAN_WAIT = 3 * 60 + 30   # three probes of ≤ 60 s each, in a row


def _launch(st, argv, launch):
    if launch:
        launch(st, argv)
    else:
        from .service_recovery import launch_job
        launch_job(st, argv, purpose="switch")


def scan(st, *, launch=None, sleep=time.sleep, clock=time.monotonic) -> dict:
    """`agentj agent switch --scan`: installed / version / callable for all three, probed by the job in the SERVICE's
    environment — what the skill reports to the owner. (A fenced Agent's own `agentj agent detect` cannot see harnesses
    whose path or key lives only in the service environment.) {"ok", "harnesses": [...], "current"} or {"ok": False, reason}."""
    job = os.urandom(8).hex()
    try:
        _launch(st, service.agentj_argv() + ["agent", "switch", "--worker", "--scan", "--job", job], launch)
    except Exception:  # noqa: BLE001
        return {"ok": False, "reason": "launch_failed"}
    end = clock() + SCAN_WAIT
    while True:
        rec = read_receipt(st, SCAN) or {}
        if rec.get("job") == job:
            return {"ok": True, "current": rec.get("current"), "harnesses": rec.get("harnesses") or []}
        if clock() >= end:
            return {"ok": False, "pending": True, "reason": "probe_pending"}
        sleep(1.0)


def scan_worker(st, job: str | None, probe=None) -> int:
    """Each harness is probed with the model a switch would give it (QA-J 10-12: OpenCode probed without its model had no
    default and timed out, though it ran fine with p155qa/gpt-6): its saved model, or the configured one for the current."""
    saved, cur = _saved(st), _raw_agent(st) or {}
    rows = []
    for name in KINDS:
        model = (cur.get("model") if cur.get("kind") == name else (saved.get(name) or {}).get("model"))
        d = (probe or harness.probe)(only=(name,), model=model if isinstance(model, str) and model else None)
        h = next((x for x in d.get("harnesses", []) if x.get("name") == name), {"name": name})
        rows.append({"name": name, "installed": bool(h.get("installed")), "version": h.get("version"),
                     "callable": bool(h.get("callable")), **({"model": model} if isinstance(model, str) and model else {}),
                     **({"reason": h["probe_reason"]} if not h.get("callable") and _CODE.fullmatch(str(h.get("probe_reason") or ""))
                        else {})})
    write_receipt(st, {"status": "scan", "job": job, "current": current_kind(st), "harnesses": rows}, SCAN)
    st.log("harness_scan", usable=[r["name"] for r in rows if r["callable"]])
    return 0


def _say_scan(r: dict, lang: str) -> str:
    if not r.get("ok"):
        return _say({**r, "target": ""}, lang) if r.get("pending") else ("✗ " + reason_text(r.get("reason") or "launch_failed", "", lang))
    en = lang == "en"
    lines = []
    for h in r["harnesses"]:
        name = LABEL.get(h["name"], h["name"])
        mark = " ←" if h["name"] == r.get("current") else ""
        if not h["installed"]:
            lines.append(f"{name}: " + ("not installed" if en else "没装"))
        elif h["callable"]:
            lines.append(f"{name} {h.get('version') or ''}: " + ("works now" if en else "现在能用") + mark)
        else:
            lines.append(f"{name} {h.get('version') or ''}: " + reason_text(h.get("reason") or "call_failed", h["name"], lang) + mark)
    can = [LABEL[h["name"]] for h in r["harnesses"] if h["callable"] and h["name"] != r.get("current")]
    lines.append(("→ can switch to: " + ", ".join(can)) if can and en else ("→ 现在能切到：" + "、".join(can)) if can else
                 ("→ only the current one works; install another one first" if en else "→ 只有现在这一个能用，要切换得先装一个别的"))
    return "\n".join(lines)


# ------------------------------------------------------------------ 2./3. the job (outside the service)
def _raw_agent(st) -> dict | None:
    try:
        a = st.config().get("agent")
    except (OSError, ValueError):
        return None
    return dict(a) if isinstance(a, dict) else None


def _restore(st, raw: dict) -> None:
    """The previous `agent` entry back exactly as it was (model, effort, isolation, docker of that harness)."""
    from .state import _write_private
    with st.config_lock():
        cfg = st.config()
        cfg["agent"] = raw
        _write_private(st.config_path, json.dumps(cfg, indent=1, ensure_ascii=False).encode())


SAVED = "harness-models.json"    # per harness: the model / effort it had when it was switched away from


def _saved(st) -> dict:
    try:
        d = json.loads((st.root / SAVED).read_text())
        return {k: v for k, v in d.items() if k in KINDS and isinstance(v, dict)} if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _apply(st, target: str, directory: str, model, effort, raw: dict) -> None:
    """The new agent entry: same folder / isolation / docker; that harness's own model and effort."""
    from .state import _write_private
    st.set_agent_config(target, directory, model, fence=raw.get("fence") is not False, docker=raw.get("docker") is True)
    if isinstance(effort, str) and effort:
        with st.config_lock():
            cfg = st.config()
            cfg["agent"]["effort"] = effort
            _write_private(st.config_path, json.dumps(cfg, indent=1, ensure_ascii=False).encode())


class _Ctl:
    def __init__(self, st, call=None):
        from .names import ctl_call
        self.st, self.call = st, call or ctl_call

    def __call__(self, req: dict) -> dict | None:
        from .names import ServeBusy
        try:
            return self.call(self.st, req, 10)
        except (ServeBusy, OSError, ValueError):
            return None


def _wait_up(ctl, kind: str, sleep, clock, limit: float = UP_WAIT) -> str | None:
    end, last = clock() + limit, "not_running"
    while True:
        s = ctl({"cmd": "status"})
        if s and s.get("ok") is True:
            if s.get("agent") != kind:
                last = "wrong_agent"
            elif s.get("agent_status") in ("idle", "working", "waiting", "compacting"):
                return None
            else:
                last = "agent_down"
        if clock() >= end:
            return last
        sleep(POLL)


def _hello(ctl, req: dict, sleep, clock) -> str | None:
    r = ctl({"cmd": "harness_hello", **req})
    if not r or r.get("ok") is not True or not isinstance(r.get("at"), (int, float)):
        return "hello_refused"
    t0, end = r["at"], clock() + HELLO_WAIT
    said = False
    while True:
        s = ctl({"cmd": "status"}) or {}
        if not said and (s.get("agent_said_at") or 0) > t0:
            said, end = True, clock() + IDLE_WAIT
        if said and s.get("agent_status") == "idle":
            return None
        if clock() >= end:
            return "not_idle" if said else "no_reply"
        sleep(POLL)


def worker(st, target: str, model: str | None = None, *, job: str | None = None, dry_run: bool = False, probe=None,
           ctl=None, restart=None, ready=None, status=None, sleep=time.sleep, clock=time.monotonic) -> int:
    ctl = ctl or _Ctl(st)
    restart = restart or service.restart
    prev = st.agent_config()
    raw = _raw_agent(st)
    version = None

    def done(status, reason=None, frm=None):
        rec = {"status": status, "target": target, "from": frm or (prev or {}).get("kind")}
        if reason:
            rec["reason"] = reason
        if job:
            rec["job"] = job
        if version:
            rec["version"] = version
        write_receipt(st, rec)
        st.log("harness_switch", status=status, frm=rec["from"], to=target, reason=reason)
        return 0 if status in ("ok", "probe_ok") else 2 if status == "rolled_back" else 1

    if target not in KINDS:
        return done("failed", "bad_target")
    if not prev or not raw:
        return done("failed", "no_agent")
    if prev["kind"] == target:
        return done("failed", "same")
    try:                            # a switch restarts the service by itself: there must be one (not a serve in a terminal)
        sv = (status or service.status)()
        if not sv.get("installed") or sv.get("active") != "active":
            return done("failed", "no_service")
    except Exception:  # noqa: BLE001
        return done("failed", "no_service")
    # the target's own model / effort from when it was last switched away from, unless the owner named one now
    saved = _saved(st)
    mine = saved.get(target) or {}
    if not model and isinstance(mine.get("model"), str):
        model = mine["model"]
    # 1. the gate, in this job's environment = the service's (launch_job), ≤ harness.PROBE_TIMEOUT
    g = gate(target, model, probe)
    version = g.get("version")
    if not g["ok"]:
        return done("failed", g["reason"])
    if dry_run:
        return done("probe_ok")
    write_receipt(st, {"status": "waiting", "target": target, "from": prev["kind"], **({"job": job} if job else {}),
                       **({"version": version} if version else {})})
    from .service_recovery import wait_ready
    if (ready or wait_ready)(st, limit=READY_LIMIT) == "not_running":
        return done("failed", "not_running")
    s = ctl({"cmd": "status"}) or {}
    stop = s.get("estop")          # serve reports {"on": bool, "at", "by"} (older: a bool)
    if (stop.get("on") is True) if isinstance(stop, dict) else stop is True:
        ctl({"cmd": "send", "text": reason_text("estop", target) + "：没有切换 / not switched"})
        return done("failed", "estop")
    saved[prev["kind"]] = {"model": raw.get("model"), "effort": raw.get("effort")}
    st.write_private(st.root / SAVED, json.dumps(saved).encode())
    _apply(st, target, prev["dir"], model, mine.get("effort") if model == mine.get("model") else None, raw)
    st.log("harness_switch", status="switching", frm=prev["kind"], to=target)
    write_receipt(st, {"status": "switching", "target": target, "from": prev["kind"]})
    try:
        restart()
        reason = _wait_up(ctl, target, sleep, clock)
    except Exception:  # noqa: BLE001
        reason = "restart_failed"
    if reason is None:
        reason = _hello(ctl, {"outcome": "ok", "from": prev["kind"], "to": target}, sleep, clock)
    if reason is None:
        return done("ok")
    # 3. roll back: the previous harness, exactly as it was, then let it explain
    _restore(st, raw)
    try:
        restart()
        back = _wait_up(ctl, prev["kind"], sleep, clock)
    except Exception:  # noqa: BLE001
        back = "restart_failed"
    told = back is None and _hello(ctl, {"outcome": "rolled_back", "from": prev["kind"], "to": target, "reason": reason},
                                   sleep, clock) is None
    if not told:
        lang = (prev or {}).get("language")
        ctl({"cmd": "send", "text": ("Switching to {t} failed ({r}); switched back to {f}." if lang == "en" else
                                     "切换到 {t} 没成功（{r}），已切回 {f}。").format(
            t=LABEL[target], f=LABEL[prev["kind"]], r=reason_text(reason, target, "en" if lang == "en" else "zh"))})
    return done("rolled_back", reason)


def service_environment() -> None:
    """The job's environment := the service's, so the gate probes exactly what the new harness will get. launch_job passes
    only PATH / state / binary paths; serve itself also has the unit's Environment= and its EnvironmentFile (Linux: every
    variable, systemd semantics) or the plist's variables + the known-name env file (macOS, load_launch_binary_env).
    Values are only placed in this process's environment, never printed or logged."""
    import plistlib
    import shlex
    n = service.name()
    try:
        if service.platform() == "macos":
            os.environ.update({k: v for k, v in plistlib.loads(Path(service.plist_path(n)).read_bytes())
                               .get("EnvironmentVariables", {}).items() if isinstance(v, str)})
            os.environ["AGENTJ_SERVICE_ENV_FILE"] = service.env_file(n)
            service.load_launch_binary_env()
            return
        for line in (Path(service.unit_dir()) / (n + ".service")).read_text().splitlines():
            if line.startswith("Environment="):
                for item in shlex.split(line.partition("=")[2]):
                    k, sep, v = item.partition("=")
                    if sep and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k):
                        os.environ[k] = v.replace("%%", "%")
    except (OSError, ValueError, service.ServiceError):
        pass
    try:
        text = Path(service.env_file(n)).read_text()
    except (OSError, UnicodeError):
        return
    for line in text.splitlines():                    # systemd EnvironmentFile: KEY=VALUE, # / ; comments, optional quotes
        line = line.strip()
        if not line or line[0] in "#;":
            continue
        k, sep, v = line.partition("=")
        k, v = k.strip(), v.strip()
        if not sep or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k):
            continue
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        os.environ[k] = v


# ------------------------------------------------------------------ the CLI
def _say(r: dict, lang: str) -> str:
    T, F = LABEL.get(r.get("target"), r.get("target")), LABEL.get(r.get("from") or "", r.get("from") or "")
    if r.get("ok") and r.get("dry_run"):
        return (f"✓ {T} passed the check (installed, answered one headless call): it can be switched to (--dry-run, nothing changed)"
                if lang == "en" else f"✓ {T} 检查通过（已安装，无头调用能正常回答）：可以切换（--dry-run，没有改任何东西）")
    if r.get("ok"):
        back = (f" If it does not come up, Agent J switches back to {F} by itself and says why." if F and lang == "en" else
                f"如果新的那边没起来，会自动切回 {F} 并说明原因。" if F else "")
        return (f"✓ {T} passed the check. Switch scheduled: when this turn ends Agent J restarts with {T}; the phone disconnects "
                f"for a few seconds and reconnects by itself, then {T} introduces itself.{back}" if lang == "en" else
                f"✓ {T} 检查通过。已安排切换：这一轮结束后 Agent J 重启并换成 {T}，手机会断开几秒再自动连上，然后 {T} 会先自报家门。{back}")
    if r.get("pending"):
        return ("… The check of {t} is still running; see the outcome with `agentj agent switch --result` in a minute.".format(t=T)
                if lang == "en" else f"… {T} 还在检查中，一分钟后用 `agentj agent switch --result` 看结果。")
    why = reason_text(r.get("reason") or "call_failed", r.get("target") or "", lang)
    return (f"✗ Cannot switch to {T}: {why}. Nothing was changed." if lang == "en" else f"✗ 不能切换到 {T}：{why}。没有改任何东西。")


def _say_receipt(rec: dict | None, lang: str) -> str:
    if not rec:
        return "No switch yet." if lang == "en" else "还没有切换过。"
    T, F = LABEL.get(rec.get("target"), rec.get("target")), LABEL.get(rec.get("from") or "", rec.get("from") or "—")
    when = time.strftime("%m-%d %H:%M", time.localtime(rec["at"]))
    st = rec.get("status")
    if st == "ok":
        return f"{when} ✓ {F} → {T}"
    if st == "rolled_back":
        return f"{when} ✗ {F} → {T}: " + reason_text(rec.get("reason") or "", rec.get("target") or "", lang) + (
            f"; back on {F}" if lang == "en" else f"；已切回 {F}")
    if st == "probe_ok":
        return f"{when} ✓ {T}: " + ("passed the check (dry run)" if lang == "en" else "检查通过（只检查，没切换）")
    if st == "failed":
        return f"{when} ✗ {F} → {T}: " + reason_text(rec.get("reason") or "", rec.get("target") or "", lang)
    return f"{when} … {F} → {T}: {st}"


def cli(a, st, lang: str) -> int:
    if getattr(a, "result", False):
        rec = read_receipt(st)
        print(json.dumps(rec or {}, ensure_ascii=False) if a.json else _say_receipt(rec, lang))
        return 0
    if getattr(a, "scan", False):
        if getattr(a, "worker", False):
            service_environment()
            return scan_worker(st, getattr(a, "job", None))
        r = scan(st)
        print(json.dumps(r, ensure_ascii=False) if a.json else _say_scan(r, lang))
        return 0 if r.get("ok") else 4 if r.get("pending") else 3
    target = getattr(a, "target", None)
    if target not in KINDS:
        print("agentj agent switch <claude|codex|opencode> [--model M] [--dry-run] [--json] · agentj agent switch --scan · --result",
              file=sys.stderr)
        return 2
    if getattr(a, "worker", False):
        service_environment()
        return worker(st, target, a.model, job=getattr(a, "job", None), dry_run=getattr(a, "dry_run", False))
    r = request(st, target, a.model, dry_run=getattr(a, "dry_run", False))
    print(json.dumps(r, ensure_ascii=False) if a.json else _say(r, lang))
    return 0 if r.get("ok") else 4 if r.get("pending") else 3
