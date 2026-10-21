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
import threading
import time
from pathlib import Path

from . import harness, opencode_free, service
from .compose import Send
from .text import clean_line

KINDS = ("claude", "codex", "opencode", "kimi", "qwen", "dsh")
if harness.names()[-1:] == ("core",):   # P359: Agent J Harness joins the switch only when the owner turned it on
    KINDS += ("core",)
LABEL = harness.LABEL
CMD = "switch-harness"
RECEIPT = "harness-switch.json"
READY_LIMIT = 300     # s the job waits for the current turn (the one that asked) to finish
UP_WAIT = 120         # s for serve + the new Agent to come up after the restart
HELLO_WAIT = 180      # s for the new Agent's self-introduction (stretched by a slow measured start, P312)
HELLO_MAX = 480       # s P312: hard cap, start included — a session still visibly starting is waited for up to here
TURN_MIN = 90         # s P312: once the session is open and the turn runs, at least this long for the answer
START_LIMIT = 150     # s P312: the start probe (app-server initialize → config/read → an ephemeral thread)
IDLE_WAIT = 120       # s for it to be idle again afterwards
POLL = 2.0
_CODE = re.compile(r"[a-z_]{1,40}")
_OC_MODEL = re.compile(r"[A-Za-z0-9._-]+/[^\s/][^\s]*")
_MODEL_MISSING = re.compile(r"model ?not ?found|modelnotfound", re.I)

REASON = {
    "zh": {"not_installed": "{x} 没有安装", "not_supported": "{x} 暂不支持",
           "native_timeout": "{x} 60 秒内没有回答（多半是没登录、token 失效，或网络 / 代理不通）",
           "native_failed": "{x} 没能完成一次调用（多半是没登录或 token 失效）", "call_failed": "{x} 没能完成一次调用（多半是没登录或 token 失效）",
           "empty_probe_reply": "{x} 回了空话", "provider_unavailable": "{x} 配置的模型服务不可用",
           "model_missing": "{x} 找不到所选的模型（服务商没配置，或模型名写错）——不是登录或 Key 的问题。下一步：在手机「模型和 API Key」换一个能用的模型再切",
           "invalid_native_config": "{x} 的配置文件有问题", "invalid_provider": "{x} 配置的模型服务地址不合规",
           "native_executable_missing": "找不到 {x} 的程序", "native_executable_not_executable": "{x} 的程序不能运行",
           "native_executable_selection_failed": "{x} 有多个安装、选不出该用哪一个", "native_spawn_failed": "{x} 启动不了",
           "native_output_limit": "{x} 输出异常", "harness_unavailable": "{x} 不可用",
           "same": "现在用的就是 {x}", "bad_target": "只能切到 Claude Code、Codex、OpenCode、Kimi Code 或 Qwen Code",
           "bad_model": "OpenCode 的模型要写成「服务商/模型」，例如 zhipuai/glm-5.3",
           "no_service": "Agent J 没有作为后台服务运行（切换要靠它自动重启；先运行 agentj service install）",
           "no_agent": "Agent J 还没接任何 Agent", "not_running": "Agent J 没在运行", "estop": "Agent J 正处于急停状态",
           "launch_failed": "安排不了切换任务", "restart_failed": "重启 Agent J 失败",
           "wrong_agent": "重启后接上的不是 {x}", "agent_down": "重启后 {x} 没有接上",
           "hello_refused": "重启后 {x} 不接收消息", "no_reply": "{x} 切换后一直没有自报家门", "not_idle": "{x} 回答后一直没有空下来",
           "start_failed": "{x} 的长驻会话打不开", "start_timeout": "{x} 的长驻会话 150 秒内没打开（MCP 服务器 / 插件太多，或Agent J 主机太忙）",
           "owner_unreachable": "设置里指定的 {x} server（agent.shared_opencode_port）连不上；让 Agent J 自己管 {x}：agentj config unset agent.shared_opencode_port",
           "owner_password": "设置里指定的 {x} server 要密码，Agent J 还没有（agentj secret request --name OPENCODE_SERVER_PASSWORD）",
           "owner_session": "设置里指定的 {x} server 里没有指定的会话（agent.shared_session_id）",
           "busy": "上一个切换 / 扫描任务还没结束，等它结束再试", "too_soon": "刚扫描过，{x} 秒后再试"},
    "en": {"not_installed": "{x} is not installed", "not_supported": "{x} is not supported yet",
           "native_timeout": "{x} did not answer within 60 s (usually not signed in, an expired token, or no network / proxy)",
           "native_failed": "{x} could not complete a call (usually not signed in or an expired token)",
           "call_failed": "{x} could not complete a call (usually not signed in or an expired token)",
           "empty_probe_reply": "{x} replied with nothing", "provider_unavailable": "{x}'s configured model service is unavailable",
           "model_missing": "{x} cannot find the selected model (its provider is not set up, or the model name is wrong) — not a "
                            "sign-in or key problem. Next: pick a working model in Models & API keys on the phone, then switch again",
           "invalid_native_config": "{x}'s config file has a problem", "invalid_provider": "{x}'s model service address is not allowed",
           "native_executable_missing": "{x}'s program was not found", "native_executable_not_executable": "{x}'s program cannot run",
           "native_executable_selection_failed": "{x} is installed more than once and none could be chosen",
           "native_spawn_failed": "{x} could not start", "native_output_limit": "{x} produced abnormal output",
           "harness_unavailable": "{x} is unavailable", "same": "{x} is already the one in use",
           "bad_target": "only Claude Code, Codex, OpenCode, Kimi Code or Qwen Code", "bad_model": "an OpenCode model is written provider/model, e.g. zhipuai/glm-5.3",
           "no_service": "Agent J is not running as a background service (the switch needs it to restart by itself; run agentj service install first)",
           "no_agent": "Agent J has no Agent configured", "not_running": "Agent J is not running", "estop": "Agent J is emergency-stopped",
           "launch_failed": "the switch job could not be scheduled", "restart_failed": "restarting Agent J failed",
           "wrong_agent": "after the restart the Agent was not {x}", "agent_down": "after the restart {x} did not come up",
           "hello_refused": "after the restart {x} did not take messages", "no_reply": "{x} never introduced itself after the switch",
           "not_idle": "{x} did not become idle after answering",
           "start_failed": "{x}'s long-running session cannot open",
           "start_timeout": "{x}'s long-running session did not open within 150 s (too many MCP servers / plugins, or a busy computer)",
           "owner_unreachable": "the {x} server named in the settings (agent.shared_opencode_port) cannot be reached; let Agent J manage {x} itself: agentj config unset agent.shared_opencode_port",
           "owner_password": "the {x} server named in the settings needs a password Agent J does not have (agentj secret request --name OPENCODE_SERVER_PASSWORD)",
           "owner_session": "the pinned session (agent.shared_session_id) is not on the {x} server named in the settings",
           "busy": "the previous switch / scan job has not finished yet; try again when it has",
           "too_soon": "a scan just ran; try again in {x} s"},
}

# P312: why a new Agent stayed silent (serve's status.agent_phase) + the owner's next step
DETAIL = {
    "zh": {"starting": "：一直还在启动（加载 MCP 服务器 / 插件、打开会话）。下一步：Agent J 主机空一些再切，或在它的配置里停用用不上的 MCP / 插件",
           "prompt": "：卡在终端里的确认提示上。下一步：在 Agent J 的工作目录里手动运行一次 {cmd}，处理掉提示再切",
           "approval": "：在等手机上的批准卡。下一步：处理掉那张卡再切",
           "failed": "：启动失败。下一步：运行 agentj doctor，修好再切",
           "turn": "：会话已打开但一直没说话（多半是模型服务慢或网络 / 代理不稳）。下一步：稍后再切；还不行在工作目录里运行 {cmd} 计时",
           "idle": "：没有接手自报家门的消息。下一步：再切一次；还不行运行 agentj doctor",
           "empty": "：自报家门那一轮一下子就结束了，一个字也没说（会话没真正接上）。下一步：运行 agentj doctor，修好再切"},
    "en": {"starting": ": still starting (loading MCP servers / plugins, opening the session). Next: switch again when the Agent J host "
                       "is less busy, or turn off unused MCP servers / plugins in its config",
           "prompt": ": stuck on a terminal prompt. Next: run {cmd} once by hand in Agent J's working folder, answer it, switch again",
           "approval": ": waiting for an approval card on the phone. Next: answer it, then switch again",
           "failed": ": it failed to start. Next: run agentj doctor, fix it, then switch again",
           "turn": ": the session opened but it never spoke (usually a slow model service or an unstable network / proxy). Next: "
                   "switch again later; if it persists, time {cmd} in the working folder",
           "idle": ": it never picked up the self-introduction. Next: switch again; if it persists run agentj doctor",
           "empty": ": the self-introduction turn ended at once without a word (the session never really connected). Next: run "
                    "agentj doctor, fix it, then switch again"},
}
ERR = {     # agent.codex_failure kinds of the harness's last stderr line
    "zh": {"auth": "没登录或登录失效（codex login）", "network": "连不上服务（查代理 / VPN）", "region": "地区不受支持",
           "quota": "额度用完", "usage": "用量到上限", "plan": "套餐不含", "workspace": "工作区不允许", "server": "服务端出错",
           "access": "访问被拒", "model": "用不了所选模型", "crash": "进程意外退出", "missing": "找不到程序", "sandbox": "沙箱拦截",
           "other": "未识别的报错"},
    "en": {"auth": "not signed in (codex login)", "network": "cannot reach the service (proxy / VPN)", "region": "region not served",
           "quota": "out of credits", "usage": "usage limit", "plan": "not in the plan", "workspace": "workspace forbids it",
           "server": "service error", "access": "access refused", "model": "model not allowed", "crash": "process crashed",
           "missing": "program not found", "sandbox": "sandbox blocked it", "other": "unrecognised error"},
}
_TRY = {"codex": 'codex exec "say hi"', "claude": 'claude -p "say hi"', "opencode": 'opencode run "say hi"'}
_TTY = {"codex": "codex", "claude": "claude", "opencode": "opencode"}
SWITCH_CLI = ("claude", "codex", "opencode", "kimi", "qwen", "dsh")   # cli.py `agentj agent switch <target>` choices

REASON["zh"].update({
    "free_tier": "{x} 的免费通道暂时不让用（刚装好的头一两分钟常见，也可能是免费额度用完或这个免费模型下线了）：过一两分钟再试，或换一个免费模型（opencode models 列出）",
    "config_mismatch": "写进去的设置读回来对不上（被别的程序改了）",
    "no_way_back": "切过去以后就切不回 {x} 了：{x} 在后台服务里也过不了同样的检查",
    "nothing_to_undo": "没有可以撤回的切换", "stale_job": "这是一个过期的切换任务，没有执行"})
REASON["en"].update({
    "free_tier": "{x}'s free tier refused for now (common in the first minute or two after installing; or the free quota is used up, or this free model was withdrawn): try again in a minute or two, or pick another free model (opencode models lists them)",
    "config_mismatch": "the setting written did not read back the same (something else changed it)",
    "no_way_back": "there would be no way back to {x}: {x} fails the same check in the background service",
    "nothing_to_undo": "there is no switch to undo", "stale_job": "a stale switch job was not run"})
# P305: Kimi Code's probe (ACP, plan mode, every tool refused)
REASON["zh"].update({"unknown_model": "{x} 没有这个模型（`kimi` 里 /model 列出可选的）",
                     "native_tool_refused": "{x} 在测试消息里想调用工具（测试只许直接回答）"})
REASON["en"].update({"unknown_model": "{x} does not offer this model (/model in kimi lists them)",
                     "native_tool_refused": "{x} tried to use a tool on the test message (the test allows a plain answer only)"})
# P423 #138: a used-up quota / a rate limit is never 「没登录」 (Kimi says its monthly limit as auth_required)
REASON["zh"].update({"quota_exhausted": "{x} 的额度 / 用量已用完——不是没登录，不用重新登录：续费或加量后再试，或先切到别的 Agent",
                     "rate_limited": "{x} 正在限流（请求太频繁）——不是没登录：等一会儿再试"})
REASON["en"].update({"quota_exhausted": "{x}'s quota / usage is used up — not a sign-in problem, no new login needed: renew or add "
                                        "usage, then retry, or switch to another Agent for now",
                     "rate_limited": "{x} is rate limited (too many requests) — not a sign-in problem: try again in a while"})
QUOTA_WHERE = {"kimi": {"zh": "（Kimi 本月额度：用量和套餐看 Kimi Code 控制台 https://www.kimi.com/code/console ，kimi.ai 会员看 "
                              "https://www.kimi.ai/code/console）",
                        "en": " (Kimi's monthly usage: see the Kimi Code console https://www.kimi.com/code/console , or "
                              "https://www.kimi.ai/code/console for kimi.ai)"}}
# P282: no login the SERVICE can see (harness.probe → not_logged_in) — a one-step way, per harness (Mac seat 10-14: Claude
# Code's token lived only in ~/.zshenv, so the service's probe counted it as "not signed in" while the owner's shell worked)
LOGIN_HINT = {
    "zh": {"claude": "后台服务找不到 {x} 的登录（登录多半只放在你终端的环境变量里，比如 ~/.zshenv 的 CLAUDE_CODE_OAUTH_TOKEN，"
                     "后台服务读不到）。办法二选一：在终端运行 claude，输入 /login 登录一次（登录会存进{store}，后台也能用）；"
                     "或在能用 claude 的终端里运行 agentj service import-key CLAUDE_CODE_OAUTH_TOKEN，再 agentj service restart",
           "codex": "后台服务找不到 {x} 的登录。在终端运行 codex login 登录一次（存进 ~/.codex/auth.json，后台也能用）；"
                    "用 API key 的话在有这个 key 的终端里运行 agentj service import-key OPENAI_API_KEY，再 agentj service restart",
           # P298: OpenCode is "logged in" with a key store / key variable OR one of its free models
           "opencode": "{x} 没有模型 key，选的也不是它的免费模型。二选一：在终端运行 opencode auth login 存一个 key；"
                       "或者先用免费模型，不用 key：agentj agent switch opencode --model opencode/<免费模型>（opencode models 列出）",
           # P305: Kimi Code — the device-code login (phone Settings → AI login rescue) or a Kimi Code console key on the key card
           "kimi": "后台服务找不到 {x} 的登录。二选一：手机 设置 →「AI 登录救援」走设备码登录（或在终端运行 kimi login --region mainland-cn，"
                   "海外站 kimi.ai 用 --region global）；或让 Agent 发手机密钥卡存 Kimi Code 控制台的 key（KIMI_API_KEY），再 agentj service restart",
           "qwen": "{x} 没有模型 key。让 Agent 发手机密钥卡存阿里百炼 Token Plan 的 key（BAILIAN_TOKEN_PLAN_API_KEY；价格和条款见 "
                   "agentj agent route），再 agentj service restart",
           # P329: DeepSeek Harness — Agent J's OpenAI-compatible route (Olares Router …) or a DeepSeek key on the key card
           "dsh": "{x} 没有模型路线。Olares / 任何 OpenAI 兼容服务：agentj agent dsh --base-url https://<Router>/v1 --model <模型 id>"
                  "（要 key 时让 Agent 发密钥卡存 DSH_ROUTER_API_KEY）；用 DeepSeek 官方 API：密钥卡存 DEEPSEEK_API_KEY，再 agentj service restart",
           # P359: Agent J Harness — built in; it needs one model key (Volcano Ark first, D9)
           "core": "{x} 没有模型 key。让 Agent 发手机密钥卡存一把模型 key（火山方舟 ARK_API_KEY；海外 BytePlus BYTEPLUS_API_KEY），"
                   "再 agentj service restart"},
    "en": {"claude": "the background service finds no {x} login (it probably lives only in your terminal's environment, e.g. "
                     "CLAUDE_CODE_OAUTH_TOKEN in ~/.zshenv, which the service cannot read). Either run claude in a terminal and "
                     "/login once (the login is stored in {store}, which the service can use too), or in a terminal where claude "
                     "works run agentj service import-key CLAUDE_CODE_OAUTH_TOKEN, then agentj service restart",
           "codex": "the background service finds no {x} login. Run codex login in a terminal once (stored in ~/.codex/auth.json, "
                    "which the service can use too); with an API key run agentj service import-key OPENAI_API_KEY in a terminal "
                    "that has it, then agentj service restart",
           "opencode": "{x} has no model key and the model chosen is not one of its free models. Either store a key with "
                       "opencode auth login in a terminal, or use a free model with no key: agentj agent switch opencode --model "
                       "opencode/<free model> (opencode models lists them)",
           "kimi": "the background service finds no {x} login. Either sign in with the device code (phone Settings → AI login rescue, or "
                   "kimi login --region mainland-cn in a terminal; --region global for kimi.ai), or have the Agent send a key card "
                   "for a Kimi Code console key (KIMI_API_KEY), then agentj service restart",
           # P310: Qwen Code — no login of its own any more (free OAuth ended 2026-04-15): a Bailian key on the key card
           "qwen": "{x} has no model key. Have the Agent send a key card for an Alibaba Cloud Bailian Token Plan key "
                   "(BAILIAN_TOKEN_PLAN_API_KEY; agentj agent route shows prices and terms), then agentj service restart",
           "dsh": "{x} has no model route. Olares or any OpenAI-compatible server: agentj agent dsh --base-url https://<router>/v1 "
                  "--model <model id> (a key card for DSH_ROUTER_API_KEY if it needs a key); DeepSeek's own API: a key card for "
                  "DEEPSEEK_API_KEY, then agentj service restart",
           "core": "{x} has no model key. Have the Agent send a key card for a model key (Volcano Ark ARK_API_KEY; abroad "
                   "BytePlus BYTEPLUS_API_KEY), then agentj service restart"},
}


def reason_text(code: str, target: str, lang: str = "zh", detail: str | None = None, err: str | None = None,
                line: str | None = None) -> str:
    """P395: `line` = the harness's own error, one redacted line from the probe (harness.probe → probe_error)."""
    l = "en" if lang == "en" else "zh"
    t = REASON[l]
    if code in harness.SIGNED_OUT_CODES and isinstance(line, str) and _MODEL_MISSING.search(line):
        code = "model_missing"            # P423 #146: "Model not found: <p>/<m>" is never 「多半是没登录或 token 失效」
    if isinstance(line, str) and line.strip() and code != "not_logged_in":
        said = LABEL.get(target, target) + (" said: " if l == "en" else " 原话：") + clean_line(line, 200)
        return reason_text(code, target, lang, detail, err) + (f" ({said})" if l == "en" else f"（{said}）")
    if code == "not_logged_in":
        hint = LOGIN_HINT[l].get(target)
        if hint:
            mac = service.platform() == "macos"
            store = (("the Keychain" if mac else "~/.claude/.credentials.json") if lang == "en" else
                     ("钥匙串" if mac else " ~/.claude/.credentials.json "))
            return hint.format(x=LABEL.get(target, target), store=store)
        code = "native_failed"
    out = t.get(code, t["call_failed"]).format(x=LABEL.get(target, target))
    if code == "quota_exhausted":
        out += QUOTA_WHERE.get(target, {}).get(l, "")
    if detail in DETAIL[l]:
        out += DETAIL[l][detail].format(cmd=(_TTY if detail == "prompt" else _TRY).get(target, target))
    if err in ERR[l]:
        out += (" (error: " + ERR[l][err] + ")" if l == "en" else "（报错：" + ERR[l][err] + "）")
    return out


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
    ark = bool(_ARK_WORDS.search(arg))       # P371: 「OpenCode · 火山方舟」 has its own one-step switch
    if lang == "en":
        head = ("[Menu command /switch-harness] The owner sent \"switch harness\" from the phone menu. Follow the bundled "
                "skill agentj-switch-harness (scan, probe headless, switch only through its gate).")
        tail = (" The owner added: " + arg) if arg else (
            " The owner added nothing: first explain in two or three plain sentences what a harness is, then scan this "
            "computer and say which ones it can switch to now; to switch, the owner replies \"switch to Claude Code / Codex / "
            "OpenCode / OpenCode · Volcano Ark / Kimi Code / Qwen Code\".")
        return head + tail + (ARK_HINT["en"] if ark else "")
    head = "〔菜单命令 /switch-harness〕主人从手机菜单发来「切换 harness」。按随包技能 agentj-switch-harness 处理（先扫描、无头探测，过了闸门才切换）。"
    tail = ("主人附加的话：" + arg) if arg else (
        "主人没有附加说明：先用两三句大白话解释什么是 harness，再扫描这台 Agent J 主机，告诉主人现在能切到哪些；"
        "要切换就回复「切换到 Claude Code / Codex / OpenCode / OpenCode · 火山方舟 / Kimi Code / Qwen Code」。")
    return head + tail + (ARK_HINT["zh"] if ark else "")


_ARK_WORDS = re.compile(r"方舟|火山|豆包|doubao|\bark\b|volcano|volcengine|seed-?2", re.I)
ARK_HINT = {   # P371
    "zh": "（这是「OpenCode · 火山方舟」：先问主人用 Coding Plan 套餐还是按量付费，再运行 "
          "`agentj agent switch opencode --preset ark --plan coding|payg`，它会探测、缺 key 时给密钥卡命令、实测一次才切。）",
    "en": " (This is \"OpenCode · Volcano Ark\": ask whether the owner has the Coding Plan or pays as they go, then run "
          "`agentj agent switch opencode --preset ark --plan coding|payg` — it detects, prints the key card if needed, and "
          "makes one real call before switching.)",
}


class HelloSend(Send):
    """The host's 「自报家门」 / 「切回来了」 turn: the Agent's ordinary queue, outside any phone page (turn 0, like the
    onboarding welcome), so the reply lands as the Agent's own message. Provenance: the owner's own host, no person."""

    def __init__(self, text: str):
        from .provenance import system
        super().__init__("harness-switch", "hello", 0, text=text, by="Agent J", source=system())


class HandoffSend(Send):
    """P289: the host turn that has the harness being switched away from write switch.md (and, after the switch, the turn
    that has the new one carry on from it). Same queue and provenance as HelloSend."""

    def __init__(self, text: str):
        from .provenance import system
        super().__init__("harness-switch", "handoff", 0, text=text, by="Agent J", source=system())


def hello_prompt(kind: str, req: dict, lang: str = "zh", where: str | None = None) -> str | None:
    """Fixed host text for serve's `harness_hello` (the job sends kinds and a code, never free text). P289: `handoff` is
    "ok" (the previous harness wrote switch.md — read it first; `where` is the host's own path for it) or "none"."""
    outcome, frm, to, code = req.get("outcome"), req.get("from"), req.get("to"), req.get("reason") or ""
    ho = req.get("handoff")
    detail, err = req.get("detail"), req.get("err")
    if outcome not in ("ok", "rolled_back") or frm not in KINDS or to not in KINDS or not (code == "" or _CODE.fullmatch(code)) \
            or ho not in (None, "ok", "none"):
        return None
    if detail not in (None, *DETAIL["zh"]) or err not in (None, *ERR["zh"]):
        return None
    if (outcome == "ok" and kind != to) or (outcome == "rolled_back" and kind != frm):
        return None
    F, T = LABEL[frm], LABEL[to]
    if lang == "en":
        if outcome == "ok" and ho == "ok" and where:
            return ("[Agent J system note — not from the owner] The owner just switched the underlying harness from " + F
                    + " to you (" + T + "). First read the handoff " + where + " completely — the essentials of the conversation "
                    "with " + F + ", written right before the switch. Then, in two or three sentences, introduce yourself to the "
                    "owner (which harness you are and which model you run — say if you are not sure; the switch is done), and "
                    "say in one sentence what you picked up from the handoff (the task and its next step). Saying \"switch "
                    "back to " + F + "\" returns to it. Use no tool other than reading that file, start no work yet, and do "
                    "not mention this note.")
        if outcome == "ok":
            return ("[Agent J system note — not from the owner] The owner just switched the underlying harness from " + F
                    + " to you (" + T + "). In one or two sentences introduce yourself to the owner: which harness you are and "
                    "which model you run (say if you are not sure), that the switch is done and the conversation can go on; the "
                    "earlier conversation stays with " + F + " — saying \"switch back to " + F + "\" returns to it."
                    + (" " + F + " could not write a handoff this time: say so in one sentence (what was said before is not "
                       "known to you; the owner may need to repeat the key points)." if ho == "none" else "")
                    + " Do not use tools and do not mention this note.")
        return ("[Agent J system note — not from the owner] The owner asked to switch to " + T + ", but the check after the "
                "switch failed (" + reason_text(code, to, "en", detail, err) + "), so Agent J switched back to you (" + F + ") by itself. In "
                "one or two sentences tell the owner: the switch did not work, you are back on the original harness, the reason "
                "in plain words, and the next step (the one the reason names, else send /switch-harness again so you re-check). Do not use tools and do "
                "not mention this note.")
    if outcome == "ok" and ho == "ok" and where:
        return ("〔Agent J 系统提示，不是主人发的〕主人刚把底层 harness 从 " + F + " 切换到了你（" + T + "）。先完整读交接文件 " + where
                + "——那是 " + F + " 切换前写的对话精要。然后用两三句话向主人自报家门（你是哪个 harness、用的什么模型，不确定就说"
                "不确定；切换已完成），再用一句话说你从交接里接上了什么（在做的事和下一步）。主人说「切回 " + F + "」就能回去。"
                "除了读这个文件不要调用别的工具，先不要开始干活，不要提这条提示。")
    if outcome == "ok":
        return ("〔Agent J 系统提示，不是主人发的〕主人刚把底层 harness 从 " + F + " 切换到了你（" + T + "）。用一两句话向主人自报家门："
                "你是哪个 harness、用的什么模型（不确定就说不确定），说明切换已完成、可以接着说话；之前的对话留在 " + F
                + " 那边，主人说「切回 " + F + "」就能回去。"
                + ("这次 " + F + " 没写成交接：用一句话告诉主人（之前说过的你不知道，要点可能得再说一遍）。" if ho == "none" else "")
                + "不要调用工具，不要提这条提示。")
    return ("〔Agent J 系统提示，不是主人发的〕主人刚才要求切换到 " + T + "，但切换后的自检没有通过（" + reason_text(code, to, "zh", detail, err)
            + "），Agent J 已经自动切回你（" + F + "）。用一两句话告诉主人：切换没成功、已经回到原来的 harness、失败原因的大白话，"
            "以及下一步建议（原因里写了下一步就照它说，否则再发一次 /switch-harness 让你重新检查）。不要调用工具，不要提这条提示。")


# ------------------------------------------------------------------ P281: what the owner sees while it runs
# The job reports a phase code (+ kinds, an index); serve turns it into these fixed host lines, so a /switch-harness that
# takes minutes never leaves the phone on an empty card. In-turn phases go on the asking page (its wait line); the rest
# (the turn has ended, Agent J restarts) are pages of their own.
PROGRESS = {
    "zh": {"scanning": "正在探测这台Agent J 主机上的 harness：Claude Code、Codex、OpenCode、Kimi Code、Qwen Code 各试一次，每个最多 1 分钟…",
           "scan_one": "正在探测 {t}（{k}/{n}），每个最多 1 分钟…",
           "probing": "正在测试 {t}：先发一条测试消息，能正常回答才切换（最多 1 分钟）…",
           "waiting": "{t} 测试通过，等这一轮说完就开始切换…",
           "switching": "正在切换到 {t}：Agent J 重启中，新的 Agent 接上后会先跟您打招呼…",
           "switched": "已切换到 {t}，它正在跟您打招呼…",
           "back": "切换到 {t} 没成功，正在切回 {f}…",
           "back_ok": "已切回 {f}，它马上跟您说明原因。"},
    "en": {"scanning": "Probing the harnesses on the Agent J host: Claude Code, Codex, OpenCode, Kimi Code and Qwen Code, one try each, up to 1 minute each…",
           "scan_one": "Probing {t} ({k}/{n}), up to 1 minute each…",
           "probing": "Trying {t}: one test message first; it switches only if that is answered (up to 1 minute)…",
           "waiting": "{t} passed the test; the switch starts when this turn is done…",
           "switching": "Switching to {t}: Agent J is restarting; the new Agent will say hello once it is up…",
           "switched": "Switched to {t}; it is saying hello…",
           "back": "Switching to {t} did not work; switching back to {f}…",
           "back_ok": "Back on {f}; it will tell you why in a moment."},
}
IN_TURN = ("scanning", "scan_one", "probing", "waiting")


def progress_text(req: dict, lang: str = "zh") -> str | None:
    """P281: the fixed line for a `harness_progress` request (None = not one of ours: unknown phase or kind)."""
    phase, to, frm = req.get("phase"), req.get("to"), req.get("from")
    t = PROGRESS["en" if lang == "en" else "zh"].get(phase) if isinstance(phase, str) else None
    if t is None or (to is not None and to not in KINDS) or (frm is not None and frm not in KINDS):
        return None
    if ("{t}" in t and to is None) or ("{f}" in t and frm is None):
        return None
    k, n = req.get("k"), req.get("n")
    if "{k}" in t and not (type(k) is int and type(n) is int and 1 <= k <= n <= len(KINDS)):
        return None
    return t.format(t=LABEL.get(to, ""), f=LABEL.get(frm, ""), k=k, n=n)


def _progress(ctl, phase: str, to: str | None = None, frm: str | None = None, **extra) -> None:
    """Best effort: an older serve answers `unknown command`, a stopped one nothing — the switch goes on either way."""
    if ctl is not None:
        ctl({"cmd": "harness_progress", "phase": phase, **({"to": to} if to else {}), **({"from": frm} if frm else {}), **extra})


# ------------------------------------------------------------------ P282: progress phases of its own
# Same protocol as P281's `harness_progress` (a phase code + kinds; serve shows its own fixed line; an older serve answers
# `unknown command` and the switch goes on). On merge with P281: PROGRESS[lang].update(PROGRESS_P282[lang]) and
# IN_TURN += ("probe_back",).
PROGRESS_P282 = {
    "zh": {"probe_back": "{t} 测试通过；再确认切过去以后还能切回 {f}（最多 1 分钟）…",
           "undo": "正在直接切回 {t}（不再预检）：Agent J 重启中，接上后它会先跟您打招呼…"},
    "en": {"probe_back": "{t} passed; now checking that {f} could be switched back to afterwards (up to 1 minute)…",
           "undo": "Switching straight back to {t} (no pre-check): Agent J is restarting; it will say hello once it is up…"},
}


def _phase(ctl, phase: str, to: str | None = None, frm: str | None = None) -> None:
    if ctl is not None:
        try:
            ctl({"cmd": "harness_progress", "phase": phase, **({"to": to} if to else {}), **({"from": frm} if frm else {})})
        except Exception:  # noqa: BLE001 — best effort, never stops a switch
            pass


# ------------------------------------------------------------------ P289: the switch handoff (switch.md)
def handoff_prompt(kind: str, req: dict, cfg: dict, lang: str = "zh") -> str | None:
    """Fixed host text for serve's `harness_handoff`: `write` → the running harness (`from`) writes switch.md before the
    switch; `resume` → the new one (`to`), switched and introduced, carries on from it. Kinds only, never free text."""
    from . import handoff
    step, frm, to = req.get("step"), req.get("from"), req.get("to")
    if step not in ("write", "resume") or frm not in KINDS or to not in KINDS or frm == to \
            or kind != (frm if step == "write" else to) or not isinstance((cfg or {}).get("dir"), str):
        return None
    where = handoff.switch_where(cfg)
    if step == "write":
        return handoff.switch_prep_text(lang, where, LABEL[frm], LABEL[to])
    return handoff.switch_resume_text(lang, where)


# Same protocol as P281's `harness_progress` (pages of their own: the asking turn is over by now). An older serve or one
# without these phases answers not-ok → the failure line goes out as a plain phone message instead (`send`).
# On merge with P281: PROGRESS[lang].update(PROGRESS_P289[lang]).
PROGRESS_P289 = {
    "zh": {"handoff": "先请 {f} 写好交接（最多 3 分钟），再切换到 {t}…",
           "handoff_ok": "{f} 的交接写好了，正在切换到 {t}；{t} 接上后会先读交接再接着干…",
           "handoff_failed": "这次没写成交接（{f} 没能在 3 分钟内写好交接文件），照样切换到 {t}；{t} 不知道之前说过什么，要点可能得再说一遍。"},
    "en": {"handoff": "Asking {f} to write the handoff first (up to 3 minutes), then switching to {t}…",
           "handoff_ok": "{f} wrote the handoff; switching to {t}, which reads it first and then carries on…",
           "handoff_failed": "No handoff this time ({f} could not write the handoff file within 3 minutes); switching to {t} anyway. "
                             "{t} does not know what was said before — the key points may need repeating."},
}


def _note(ctl, phase: str, to: str, frm: str, lang: str) -> None:
    """A P289 phase for the owner: P281's progress page when serve knows it, else (failure only) a plain phone message."""
    r = None
    if ctl is not None:
        try:
            r = ctl({"cmd": "harness_progress", "phase": phase, "to": to, "from": frm})
        except Exception:  # noqa: BLE001
            r = None
    if (not r or r.get("ok") is not True) and phase == "handoff_failed" and ctl is not None:
        L = "en" if lang == "en" else "zh"
        try:
            ctl({"cmd": "send", "text": PROGRESS_P289[L][phase].format(f=LABEL[frm], t=LABEL[to])})
        except Exception:  # noqa: BLE001
            pass


def _write_handoff(st, ctl, cfg: dict, frm: str, to: str, sleep, clock, limit: float | None = None) -> str:
    """Before the switch: the running harness writes switch.md in a host turn. "ok" = the file is new or changed since the
    turn was asked for (mtime, size, sha256 — like compactprep); otherwise a reason code. Never blocks the switch."""
    from . import handoff
    lang = cfg.get("language")
    limit = handoff.SWITCH_WAIT if limit is None else limit
    before = handoff.switch_probe(cfg)
    _note(ctl, "handoff", to, frm, lang)
    r = ctl({"cmd": "harness_handoff", "step": "write", "from": frm, "to": to})
    if not r or r.get("ok") is not True or not isinstance(r.get("at"), (int, float)):
        res = "refused"
    else:
        t0, end, res = r["at"], clock() + limit, "timeout"
        while True:
            after = handoff.switch_probe(cfg)
            s = ctl({"cmd": "status"}) or {}
            said = (s.get("agent_said_at") or 0) > t0
            if after is not None and after != before and s.get("agent_status") == "idle":
                res = "ok"
                break
            if said and s.get("agent_status") == "idle":       # answered and idle — but no new file
                res = "missing" if after is None else "unchanged"
                break
            if clock() >= end:
                res = "timeout" if after is None or after == before else "ok_busy"
                break
            sleep(POLL)
    ok = res in ("ok", "ok_busy")
    st.log("harness_handoff", step="write", result=res, frm=frm, to=to)
    _note(ctl, "handoff_ok" if ok else "handoff_failed", to, frm, lang)
    return "ok" if ok else res


# merged (P290): P282's and P289's phases go through P281's one table; probe_back runs inside the asking turn
for _l in ("zh", "en"):
    PROGRESS[_l].update(PROGRESS_P282[_l])
    PROGRESS[_l].update(PROGRESS_P289[_l])
IN_TURN += ("probe_back",)


# ------------------------------------------------------------------ receipts (fixed codes only)
SCAN = "harness-scan.json"
PREVIOUS = "harness-previous.json"   # P282: the agent entry before the last switch — what --undo restores, no gate
JOB_MAX_AGE = 15 * 60                 # s: a job launched longer ago is stale (a leftover launchd plist replayed at login)


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
    return {"ok": False, "reason": code if _CODE.fullmatch(code) else "call_failed", "version": h.get("version"),
            **({"line": h["probe_error"]} if isinstance(h.get("probe_error"), str) else {})}   # P395


def start_probe(st, target: str, cfg: dict, limit: float = START_LIMIT) -> dict | None:
    """P312: the start the session will make, measured before the switch (Codex: agent_codex.start_probe; P326 OpenCode:
    opencode_start — our own `opencode serve`, or the owner's server named in the settings)."""
    if target == "opencode":
        from .opencode_start import start_probe as opencode
        return opencode(st, cfg, limit)
    if target != "codex":
        return None
    from .agent_codex import start_probe as codex
    return codex(st, cfg, limit)


def current_kind(st) -> str | None:
    try:
        return (st.agent_config() or {}).get("kind")
    except Exception:  # noqa: BLE001 — inside the fence the state is hidden: the job checks again
        return None


# s the caller waits for the job's verdict: the target's probe + (P282) the way back's, ≤ 60 s each, + (P298) the free tier's
# warm-up retries for the one of them that is an OpenCode free model
PROBE_WAIT = 150 + opencode_free.WARM_EXTRA


def request(st, target: str | None, model: str | None = None, dry_run: bool = False, *, launch=None,
            sleep=time.sleep, clock=time.monotonic, force: bool = False, undo: bool = False) -> dict:
    """Pre-checks here; the gate itself runs in the job, because the job has the SERVICE's environment — the one the new
    harness will run with (a key that lives only in the service env is invisible to a fenced caller, and the caller's own
    shell may hold keys the service lacks). The caller waits for the verdict: failed (nothing changed) · probe_ok (dry run) ·
    waiting (passed; the switch follows when this turn ends).
    P282: the job also checks the way back (the current harness) unless `force`; `undo` restores the entry from before the
    last switch with no gate at all (the switch's own restart check and roll-back still apply)."""
    if undo:
        prev = _previous(st)
        if not prev:
            return {"ok": False, "reason": "nothing_to_undo", "target": None}
        target = prev["kind"]
    if target not in KINDS:
        return {"ok": False, "reason": "bad_target", "target": target}
    from .slash import MODEL_RE
    if model and not (_OC_MODEL.fullmatch(model) if target == "opencode" else MODEL_RE.fullmatch(model)):
        return {"ok": False, "reason": "bad_model", "target": target}
    cur = current_kind(st)
    if cur == target:
        return {"ok": False, "reason": "same", "target": target, "from": cur}
    # the service check is the job's: inside the fence the unit is invisible (QA-J 10-12)
    spec = {"kind": "switch", "target": None if undo else target, "model": model if model and not undo else None,
            "dry_run": bool(dry_run), "force": bool(force), "undo": bool(undo)}
    try:        # P339 review: serve picks the job id (and writes "probing"); launched here, the caller does as before
        job = _launch(st, spec, launch, lambda j: write_receipt(st, {"status": "probing", "target": target, "from": cur, "job": j}))
    except HostRefused as e:
        if e.args[0] in _WAIT:      # another job is in flight: its receipt stays
            return {"ok": False, "reason": e.args[0], "target": target, "from": cur}
        write_receipt(st, {"status": "failed", "target": target, "from": cur, "reason": "launch_failed"})
        return {"ok": False, "reason": "launch_failed", "target": target, "from": cur}
    except Exception:  # noqa: BLE001
        write_receipt(st, {"status": "failed", "target": target, "from": cur, "reason": "launch_failed"})
        return {"ok": False, "reason": "launch_failed", "target": target, "from": cur}
    end = clock() + PROBE_WAIT
    while True:
        rec = read_receipt(st) or {}
        if rec.get("job") == job and rec.get("status") != "probing":
            break
        if clock() >= end:
            return {"ok": False, "pending": True, "reason": "probe_pending", "target": target, "from": cur}
        sleep(1.0)
    m = rec.get("model") if isinstance(rec.get("model"), str) else None
    note = opencode_free.privacy_note(m, _lang_of(st)) if target == "opencode" else None
    if target == "opencode" and not note:            # P310: Bailian Token Plan terms ride the same "privacy" field
        from .bailian import model_note
        from .ark import model_note as ark_note     # P315
        note = model_note(m, "en" if _lang_of(st) == "en" else "zh") or ark_note(m, "en" if _lang_of(st) == "en" else "zh")
    base = {"target": target, "from": rec.get("from") or cur, "version": rec.get("version"),
            **({"model": m} if m else {}), **({"privacy": note} if note else {}),
            **({"back": rec["back"]} if isinstance(rec.get("back"), str) else {}), **({"undo": True} if undo else {}),
            **({"undo_possible": True} if not undo and (_previous(st) or {}).get("kind") == target else {}),
            **{k: rec[k] for k in ("err", "start_secs", "line", "ark_plan_fix") if k in rec}}
    if rec["status"] == "probe_ok":
        return {"ok": True, "dry_run": True, **base}
    if rec["status"] == "failed":
        return {"ok": False, "reason": rec.get("reason") or "call_failed", **base}
    return {"ok": True, "scheduled": True, **base}


SCAN_WAIT = 5 * 60 + 30 + opencode_free.WARM_EXTRA   # P310: five probes of ≤ 60 s each, in a row (+ P298 warm-up)


def job_argv(spec: dict) -> list[str]:
    if spec["kind"] == "scan":
        return service.agentj_argv() + ["agent", "switch", "--worker", "--scan", "--job", spec["job"]]
    return service.agentj_argv() + ["agent", "switch"] + (["--undo"] if spec.get("undo") else [spec["target"]]) \
        + ["--worker", "--job", spec["job"]] + (["--model", spec["model"]] if spec.get("model") and not spec.get("undo") else []) \
        + (["--dry-run"] if spec.get("dry_run") else []) + (["--force"] if spec.get("force") else [])


class HostRefused(Exception):
    pass


_JOB = re.compile(r"[0-9a-f]{16}")
_SPEC_KEYS = frozenset({"kind", "target", "model", "dry_run", "force", "undo", "at"})   # P339 review: the job id is serve's
_WAIT = ("busy", "too_soon")
LIVE = ("probing", "waiting", "switching")
SCAN_EVERY = 60       # s: at most one scan (five real model calls) per minute; a fresher result is handed out again
_LOCK = threading.Lock()
_LAST_SCAN: list = []  # [job, time.time()] of the scan this serve launched last


def check_spec(req) -> tuple[dict | None, str | None]:
    """P339 (serve): every field checked; never an argv / env from the caller. → (spec, None) | (None, code)."""
    if not isinstance(req, dict):
        return None, "bad_request"
    req = {k: v for k, v in req.items() if k not in ("cmd", "t")}
    if set(req) - _SPEC_KEYS:
        return None, "bad_field"
    kind = req.get("kind")
    if kind not in ("switch", "scan"):
        return None, "bad_kind"
    flags = {}
    for k in ("dry_run", "force", "undo"):
        v = req.get(k, False)
        if type(v) is not bool:
            return None, "bad_" + k
        flags[k] = v
    at = req.get("at")
    if at is not None and (type(at) not in (int, float) or abs(time.time() - at) > JOB_MAX_AGE):
        return None, "bad_at"
    target, model = req.get("target"), req.get("model")
    if kind == "scan":
        if target is not None or model is not None or any(flags.values()):
            return None, "bad_field"
        return {"kind": "scan"}, None
    if flags["undo"]:
        if target is not None or model is not None:
            return None, "bad_field"
    elif target not in KINDS:
        return None, "bad_target"
    if model is not None:
        from .slash import MODEL_RE
        if not (isinstance(model, str) and (_OC_MODEL.fullmatch(model) if target == "opencode" else MODEL_RE.fullmatch(model))):
            return None, "bad_model"
    return {"kind": "switch", "target": target, "model": model, **flags}, None


def _admit(st, kind: str) -> dict | None:
    """P339 review: one switch / scan job in flight at a time; a scan at most once per SCAN_EVERY. None = launch."""
    now, rec, scan = time.time(), read_receipt(st) or {}, read_receipt(st, SCAN) or {}
    if rec.get("status") in LIVE and now - rec["at"] < JOB_MAX_AGE:
        return {"ok": False, "error": "busy"}
    job, at = _LAST_SCAN or (None, 0.0)
    if kind == "scan":
        if _JOB.fullmatch(str(scan.get("job"))) and now - scan["at"] < SCAN_EVERY:
            return {"ok": True, "job": scan["job"]}               # fresh: the same result again, no new model calls
        if now - at < SCAN_EVERY:
            return {"ok": False, "error": "too_soon", "wait": int(SCAN_EVERY - (now - at)) + 1}
    if job and scan.get("job") != job and now - at < SCAN_WAIT:
        return {"ok": False, "error": "busy"}
    return None


def host_job(st, req, launch_job=None) -> dict:
    """P339: serve launches the job for any caller (a fenced one cannot reach launchd / systemd). Blocking: in a thread."""
    spec, err = check_spec(req)
    if err:
        st.log("harness_job", result="refused", reason=err)
        return {"ok": False, "error": err}
    if launch_job is None:
        from .service_recovery import launch_job
    with _LOCK:
        held = _admit(st, spec["kind"])
        if held:
            st.log("harness_job", result=held.get("error") or "reused", kind=spec["kind"])
            return held
        job = spec["job"] = os.urandom(8).hex()
        if spec["kind"] == "switch":
            write_receipt(st, {"status": "probing", "target": spec["target"] or (_previous(st) or {}).get("kind"),
                               "from": current_kind(st), "job": job})
        try:
            launch_job(st, job_argv(spec), purpose="switch", extra_env={"AGENTJ_JOB_AT": str(int(time.time()))})
        except Exception as e:  # noqa: BLE001
            code = e.reason if isinstance(e, service.ServiceError) and e.reason in service.NO_MANAGER else "launch_failed"
            if spec["kind"] == "switch":
                write_receipt(st, {"status": "failed", "reason": "launch_failed", "job": job})
            st.log("harness_job", result="failed", kind=spec["kind"], reason=code)
            return {"ok": False, "error": code}
        if spec["kind"] == "scan":
            _LAST_SCAN[:] = [job, time.time()]
    st.log("harness_job", result="launched", kind=spec["kind"])
    return {"ok": True, "job": job}


def _via_serve(st, spec: dict) -> str | None:
    """serve's job id = launched by serve; None = no / older serve; HostRefused(code, wait). elevate.sock, then control.sock."""
    from .elevate import client_request
    from .names import ServeBusy, ctl_call, elevate_sock
    req = {**spec, "at": int(time.time())}
    r = client_request(st, {"t": "harness_job", **req}, timeout=30, path=elevate_sock(st))
    if r.get("ok") is None:
        try:
            r = ctl_call(st, {"cmd": "harness_job", **req}, 30)
        except ServeBusy:
            return None
        if r is None or r.get("error") == "unknown command":
            return None
    if r.get("ok") is True and _JOB.fullmatch(str(r.get("job"))):
        return r["job"]
    raise HostRefused(str(r.get("error") or "refused"), r.get("wait"))


def _launch(st, spec, launch, before=None) -> str:
    """→ the job id the caller waits on: serve's, or (no serve / an older one / a test's `launch`) a fresh one launched here
    after `before(job)` (the receipt a switch caller writes first)."""
    job = None if launch else _via_serve(st, spec)
    if job:
        return job
    job = os.urandom(8).hex()
    if before:
        before(job)
    argv = job_argv({**spec, "job": job})
    if launch:
        launch(st, argv)
        return job
    from .service_recovery import launch_job
    launch_job(st, argv, purpose="switch", extra_env={"AGENTJ_JOB_AT": str(int(time.time()))})
    return job


def scan(st, *, launch=None, sleep=time.sleep, clock=time.monotonic) -> dict:
    """`agentj agent switch --scan`: installed / version / callable for all three, probed by the job in the SERVICE's
    environment — what the skill reports to the owner. (A fenced Agent's own `agentj agent detect` cannot see harnesses
    whose path or key lives only in the service environment.) {"ok", "harnesses": [...], "current"} or {"ok": False, reason}."""
    try:
        job = _launch(st, {"kind": "scan"}, launch)
    except HostRefused as e:
        return {"ok": False, "reason": e.args[0] if e.args[0] in _WAIT else "launch_failed",
                **({"wait": e.args[1]} if type(e.args[1]) is int else {})}
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


def scan_worker(st, job: str | None, probe=None, ctl=None) -> int:
    """Each harness is probed with the model a switch would give it (QA-J 10-12: OpenCode probed without its model had no
    default and timed out, though it ran fine with p155qa/gpt-6): its saved model, or the configured one for the current."""
    cur = _raw_agent(st) or {}
    ctl = ctl if ctl is not None else (_Ctl(st) if probe is None else None)
    rows = []
    _progress(ctl, "scanning")
    for k, name in enumerate(KINDS, 1):
        _progress(ctl, "scan_one", name, k=k, n=len(KINDS))
        model = _probe_model(st, name, cur)           # P219: Claude without a model = what a switch to it would run
        d = (probe or harness.probe)(only=(name,), model=model if isinstance(model, str) and model else None)
        h = next((x for x in d.get("harnesses", []) if x.get("name") == name), {"name": name})
        rows.append({"name": name, "installed": bool(h.get("installed")), "version": h.get("version"),
                     "callable": bool(h.get("callable")), **({"model": model} if isinstance(model, str) and model else {}),
                     **({"reason": h["probe_reason"]} if not h.get("callable") and _CODE.fullmatch(str(h.get("probe_reason") or ""))
                        else {}), **_where(h),
                     **({"line": h["probe_error"]} if not h.get("callable") and isinstance(h.get("probe_error"), str) else {}),
                     **({"login": "free"} if h.get("login") == "free" else {}),        # P298: OpenCode free model, no key
                     **({"privacy": opencode_free.privacy_note(model, _lang_of(st))}
                        if name == "opencode" and opencode_free.privacy_note(model) else {})})
    write_receipt(st, {"status": "scan", "job": job, "current": current_kind(st), "harnesses": rows}, SCAN)
    st.log("harness_scan", usable=[r["name"] for r in rows if r["callable"]])
    return 0


def _where(h: dict) -> dict:
    """P282: which program the service runs (paths only, ~-shortened) — a saved path left behind by a self-update that was
    bypassed (`pinned`), and other installs with another version (which one is old, so the owner can remove it)."""
    from .service import tilde
    out = {}
    if isinstance(h.get("executable"), str):
        out["executable"] = tilde(h["executable"])
    res = h.get("resolution") or {}
    if isinstance(res.get("pinned"), str) and res.get("reason") in ("pin_stale", "pin_missing"):
        out["pinned"] = {"path": tilde(res["pinned"]), "why": res["reason"]}
    others = [{"path": tilde(i["path"]), "version": i.get("version")} for i in h.get("installations") or []
              if isinstance(i, dict) and not i.get("selected") and not i.get("wrapper") and i.get("version")
              and i.get("version") != h.get("version") and i.get("path") != res.get("pinned")]
    if others:
        out["others"] = others[:5]
    return out


def _say_scan(r: dict, lang: str) -> str:
    if not r.get("ok"):
        return _say({**r, "target": ""}, lang) if r.get("pending") else ("✗ " + reason_text(r.get("reason") or "launch_failed", str(r.get("wait", "")), lang))
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
            lines.append(f"{name} {h.get('version') or ''}: " + reason_text(h.get("reason") or "call_failed", h["name"], lang,
                                                                          line=h.get("line")) + mark)
        lines.extend("  " + x for x in _where_lines(h, lang))
        if h.get("privacy"):
            lines.append("  " + h["privacy"])
    ok = [h["name"] for h in r["harnesses"] if h["callable"] and h["name"] != r.get("current")]
    can = [LABEL[n] for n in ok]
    lines.append(("→ can switch to: " + ", ".join(can)) if can and en else ("→ 现在能切到：" + "、".join(can)) if can else
                 ("→ only the current one works; install another one first" if en else "→ 只有现在这一个能用，要切换得先装一个别的"))
    # P423 #138: the command to copy, per harness the `agentj agent switch` CLI takes (its argparse choices)
    lines.extend("  agentj agent switch " + n for n in ok if n in SWITCH_CLI)
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


def _reads_back(st, target: str, model) -> bool:
    """P298: the agent entry read back from config.json is the harness (and model) just written."""
    a = _raw_agent(st) or {}
    want = model if isinstance(model, str) and model else None
    have = a.get("model") if isinstance(a.get("model"), str) and a.get("model") else None
    return a.get("kind") == target and have == want


def _lang_of(st) -> str:
    try:
        from . import preferences
        return "en" if preferences.get(preferences.effective(st), "appearance.language", "zh") == "en" else "zh"
    except Exception:  # noqa: BLE001
        return "zh"


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


def _hello(ctl, req: dict, sleep, clock, wait: float = HELLO_WAIT) -> dict | None:
    """None = answered and idle; else {"reason", "detail"?, "err"?, "waited"}. P312: a visibly `starting` Agent is waited for
    (≤ HELLO_MAX), an open turn gets TURN_MIN, a failed start ends the wait."""
    r = ctl({"cmd": "harness_hello", **req})
    if not r or r.get("ok") is not True or not isinstance(r.get("at"), (int, float)):
        return {"reason": "hello_refused"}
    t0, start = r["at"], clock()
    end, cap = start + wait, start + HELLO_MAX
    said, phase, err, turn_seen = False, None, None, False
    while True:
        s = ctl({"cmd": "status"}) or {}
        ph, er = s.get("agent_phase"), s.get("agent_err")
        if isinstance(ph, str) and ph in DETAIL["zh"]:
            phase = ph
        if isinstance(er, str) and er in ERR["zh"]:
            err = er
        if not said and (s.get("agent_said_at") or 0) > t0:
            said, end = True, clock() + IDLE_WAIT
        if said and s.get("agent_status") == "idle":
            return None
        ts, te = s.get("agent_turn_start_at") or 0, s.get("agent_turn_end_at") or 0
        if not said and ts >= t0 and te >= ts and s.get("agent_status") == "idle":
            # P326: the hello turn already ended without a word (Mac 10-15: one second, then three minutes of waiting) —
            # a failed start or an empty turn, said now, not after HELLO_WAIT
            phase = "failed" if phase == "failed" or s.get("agent_turn_failed") else "empty"
            end = clock()
        if not said:
            if phase == "failed":
                end = clock()
            elif phase == "turn" and not turn_seen:          # the session opened: the answer gets its own time
                turn_seen, end = True, max(end, min(cap, clock() + TURN_MIN))
            elif phase == "starting" and clock() >= end:      # still visibly starting: keep waiting, bounded
                end = min(cap, clock() + 2 * POLL)
        if clock() >= end:
            out = {"reason": "not_idle" if said else "no_reply", "waited": int(clock() - start)}
            if not said and phase:
                out["detail"] = phase
            if err:
                out["err"] = err
            return out
        sleep(POLL)


def _previous(st) -> dict | None:
    try:
        d = json.loads((st.root / PREVIOUS).read_text())
    except (OSError, ValueError):
        return None
    raw = d.get("raw") if isinstance(d, dict) else None
    return d if isinstance(raw, dict) and raw.get("kind") in KINDS and d.get("kind") == raw["kind"] else None


def _remember(st, raw: dict, to: str) -> None:
    st.write_private(st.root / PREVIOUS, json.dumps({"kind": raw.get("kind"), "to": to, "raw": raw, "at": time.time()}).encode())


def _probe_model(st, kind: str, raw: dict | None):
    """The model a probe of `kind` should use: the configured one for the current harness, else the saved one, else (Claude)
    Agent J's default — what a switch to it would run."""
    raw = raw or {}
    model = raw.get("model") if raw.get("kind") == kind else (_saved(st).get(kind) or {}).get("model")
    if kind == "claude" and not model:
        from .harness_setup import DEFAULT_CLAUDE_MODEL
        model = DEFAULT_CLAUDE_MODEL
    return model if isinstance(model, str) and model else None


def worker(st, target: str | None, model: str | None = None, *, job: str | None = None, dry_run: bool = False, probe=None,
           ctl=None, restart=None, ready=None, status=None, sleep=time.sleep, clock=time.monotonic,
           force: bool = False, undo: bool = False, start=None) -> int:
    ctl = ctl or _Ctl(st)
    if start is None:               # an injected gate probe is a test: no real app-server unless a start probe is injected too
        start = start_probe if probe is None else (lambda *a, **k: None)
    restart = restart or service.restart
    prev = st.agent_config()
    raw = _raw_agent(st)
    version, extra = None, {}
    back_reason = None
    back_raw = None
    if undo:                          # P282: the entry from before the last switch, exactly as it was — no gate
        p = _previous(st)
        if p:
            back_raw, target = p["raw"], p["kind"]

    def done(status, reason=None, frm=None):
        rec = {"status": status, "target": target, "from": frm or (prev or {}).get("kind"), **extra}
        if reason:
            rec["reason"] = reason
        if back_reason:
            rec["back"] = back_reason
        if undo:
            rec["undo"] = True
        if job:
            rec["job"] = job
        if version:
            rec["version"] = version
        if isinstance(model, str) and model and not undo:
            rec["model"] = model
        write_receipt(st, rec)
        st.log("harness_switch", status=status, frm=rec["from"], to=target, reason=reason,
               **{k: extra[k] for k in ("detail", "err", "start_secs", "waited") if k in extra})
        return 0 if status in ("ok", "probe_ok") else 2 if status == "rolled_back" else 1

    if undo and back_raw is None:
        return done("failed", "nothing_to_undo")
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
    wait = HELLO_WAIT
    if undo:
        effort = None
    else:
        # the target's own model / effort from when it was last switched away from, unless the owner named one now
        saved = _saved(st)
        mine = saved.get(target) or {}
        if not model and isinstance(mine.get("model"), str):
            model = mine["model"]
        default_claude = target == "claude" and not model
        if default_claude:               # P219: Agent J's Claude Code default, never Claude Code's own pick (a relay may 400 it)
            from .harness_setup import DEFAULT_CLAUDE_MODEL
            model = DEFAULT_CLAUDE_MODEL
        # 1. the gate, in this job's environment = the service's (launch_job), ≤ harness.PROBE_TIMEOUT
        _progress(ctl, "probing", target)
        g = gate(target, model, probe)
        version = g.get("version")
        if not g["ok"]:
            if target == "opencode" and isinstance(g.get("line"), str) and re.search(r"\b401\b", g["line"]):
                from . import ark_plan_fix
                fix = ark_plan_fix.diagnose(st, model, force=force)
                if fix:
                    extra["ark_plan_fix"] = fix
            if g.get("line"):
                extra["line"] = g["line"]         # P395: OpenCode's own error, one redacted line, into the receipt
            return done("failed", g["reason"])
        # P282: and the way back — the harness running now must pass the same gate, or a later 「切回」 is refused by it
        # (Mac seat 10-14: OpenCode was the only one the service could call; switching to it would have been one-way)
        if not force:
            _phase(ctl, "probe_back", target, prev["kind"])
            gb = gate(prev["kind"], _probe_model(st, prev["kind"], raw), probe)
            if not gb["ok"]:
                back_reason = gb["reason"]
                return done("failed", "no_way_back")
        m = start(st, target, prev)  # P312: the start the session will make (same folder / launch), before anything changes
        if m:
            extra["start_secs"] = int(m.get("secs") or 0)
            if m.get("err"):
                extra["err"] = m["err"]
            if m.get("reason"):
                return done("failed", m["reason"])
        wait = min(HELLO_MAX, HELLO_WAIT + 2 * extra.get("start_secs", 0))
        if dry_run:
            return done("probe_ok")
    write_receipt(st, {"status": "waiting", "target": target, "from": prev["kind"], **({"job": job} if job else {}),
                       **({"version": version} if version else {}), **({"undo": True} if undo else {}),
                       **({"model": model} if isinstance(model, str) and model and not undo else {}), **extra})
    _progress(ctl, "waiting", target)
    from .service_recovery import wait_ready
    if (ready or wait_ready)(st, limit=READY_LIMIT) == "not_running":
        return done("failed", "not_running")
    s = ctl({"cmd": "status"}) or {}
    stop = s.get("estop")          # serve reports {"on": bool, "at", "by"} (older: a bool)
    if (stop.get("on") is True) if isinstance(stop, dict) else stop is True:
        ctl({"cmd": "send", "text": reason_text("estop", target) + "：没有切换 / not switched"})
        return done("failed", "estop")
    # P289: the harness being left writes switch.md now — after the gate and the way back, before anything changes; a
    # failed or timed-out handoff never stops the switch (the owner is told, and so is the new harness)
    ho = _write_handoff(st, ctl, prev, prev["kind"], target, sleep, clock)
    if undo:
        _phase(ctl, "undo", target, prev["kind"])
        _restore(st, back_raw)
    else:
        saved[prev["kind"]] = {"model": raw.get("model"), "effort": raw.get("effort")}
        st.write_private(st.root / SAVED, json.dumps(saved).encode())
        effort = mine.get("effort") if model == mine.get("model") else None
        if default_claude and not effort:
            from .harness_setup import DEFAULT_CLAUDE_EFFORT
            effort = DEFAULT_CLAUDE_EFFORT
        _apply(st, target, prev["dir"], model, effort, raw)
        if not _reads_back(st, target, model):     # P298: the script checks what it wrote, not anyone's account of it
            _restore(st, raw)
            return done("failed", "config_mismatch")
    _remember(st, raw, target)       # P282: what --undo goes back to
    st.log("harness_switch", status="switching", frm=prev["kind"], to=target)
    write_receipt(st, {"status": "switching", "target": target, "from": prev["kind"], **({"undo": True} if undo else {})})
    _progress(ctl, "switching", target, prev["kind"])
    try:
        restart()
        reason = _wait_up(ctl, target, sleep, clock)
    except Exception:  # noqa: BLE001
        reason = "restart_failed"
    if reason is None:
        _progress(ctl, "switched", target, prev["kind"])
        h = _hello(ctl, {"outcome": "ok", "from": prev["kind"], "to": target, "handoff": "ok" if ho == "ok" else "none"},
                   sleep, clock, wait)
        if h is not None:
            reason = h["reason"]
            extra.update({k: h[k] for k in ("detail", "err", "waited") if k in h})
    if reason is None:
        if not undo and not _reads_back(st, target, model):
            st.log("harness_switch", status="config_drift", to=target)   # changed after the switch (e.g. by the new Agent)
        if ho == "ok":                 # P289: introduced and verified — now carry on from the handoff (like P151's recovery)
            r = ctl({"cmd": "harness_handoff", "step": "resume", "from": prev["kind"], "to": target})
            st.log("harness_handoff", step="resume", result="sent" if r and r.get("ok") is True else "refused",
                   frm=prev["kind"], to=target)
        return done("ok")
    # 3. roll back: the previous harness, exactly as it was, then let it explain
    _progress(ctl, "back", target, prev["kind"])
    _restore(st, raw)
    try:
        restart()
        back = _wait_up(ctl, prev["kind"], sleep, clock)
    except Exception:  # noqa: BLE001
        back = "restart_failed"
    if back is None:
        _progress(ctl, "back_ok", target, prev["kind"])
    why = {k: extra[k] for k in ("detail", "err") if k in extra}
    told = back is None and _hello(ctl, {"outcome": "rolled_back", "from": prev["kind"], "to": target, "reason": reason, **why},
                                   sleep, clock) is None
    if not told:
        lang = (prev or {}).get("language")
        ctl({"cmd": "send", "text": ("Switching to {t} failed ({r}); switched back to {f}." if lang == "en" else
                                     "切换到 {t} 没成功（{r}），已切回 {f}。").format(
            t=LABEL[target], f=LABEL[prev["kind"]], r=reason_text(reason, target, "en" if lang == "en" else "zh",
                                                                  why.get("detail"), why.get("err")))})
    return done("rolled_back", reason)


def stale_job(st, job: str | None, environ=None) -> bool:
    """P282: a job is run once, right after its caller launched it. A leftover launchd plist (RunAtLoad) replays it at the
    next login (seen on the Mac seat 10-14: three scan jobs left loaded): too old by its launch time — it does nothing."""
    e = os.environ if environ is None else environ
    try:
        at = float(e.get("AGENTJ_JOB_AT") or "")
    except ValueError:
        at = None
    return at is not None and not (-60 <= time.time() - at <= JOB_MAX_AGE)


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
def _where_lines(h: dict, lang: str) -> list[str]:
    en, out = lang == "en", []
    pin = h.get("pinned")
    if isinstance(pin, dict) and h.get("executable"):
        out.append((f"(the service had saved {pin['path']}, which is gone or an older version; it now uses {h['executable']} "
                    f"and its saved path was updated)") if en else
                   f"（后台服务原先记着 {pin['path']}，那是已删除或更旧的版本；现已改用 {h['executable']}，记录也已更新）")
    for o in h.get("others") or []:
        out.append((f"(another install: {o['path']} = {o['version']}; not used — remove it if you do not need it)") if en else
                   f"（另有一份 {o['path']} = {o['version']}，没在用；不需要可以删掉）")
    return out


def _say(r: dict, lang: str) -> str:
    if r.get("ark_plan_fix"):
        from .ark_plan_fix import guidance
        return _say({k:v for k,v in r.items() if k != "ark_plan_fix"}, lang) + "\n" + guidance(r["ark_plan_fix"], lang)
    T, F = LABEL.get(r.get("target"), r.get("target")), LABEL.get(r.get("from") or "", r.get("from") or "")
    if r.get("ok") and r.get("privacy"):        # P298: a free model whose provider keeps / trains on the conversation
        return _say({k: v for k, v in r.items() if k != "privacy"}, lang) + "\n" + r["privacy"]
    if r.get("ok") and r.get("dry_run"):
        return (f"✓ {T} passed the check (installed, answered one headless call"
                + (", its long-running session opens" if isinstance(r.get("start_secs"), int) else "") + "): it can be switched to (--dry-run, nothing changed)"
                if lang == "en" else f"✓ {T} 检查通过（已安装，无头调用能正常回答" + ("，长驻会话能打开" if isinstance(r.get("start_secs"), int) else "")
                + "）：可以切换（--dry-run，没有改任何东西）")
    slow = r.get("start_secs") if isinstance(r.get("start_secs"), int) and r["start_secs"] >= 20 else None
    if r.get("ok") and slow:          # P312: said before the switch, so a long wait after the restart is expected
        r = {**r, "note": (f" Its session took {slow} s to start here, so the switch waits longer for it." if lang == "en" else
                           f"这台Agent J 主机上它的会话启动要 {slow} 秒，切换后会多等它一会儿。")}
    if r.get("ok"):
        back = (f" If it does not come up, Agent J switches back to {F} by itself and says why." if F and lang == "en" else
                f"如果新的那边没起来，会自动切回 {F} 并说明原因。" if F else "")
        return (f"✓ {T} passed the check. Switch scheduled: when this turn ends Agent J restarts with {T}; the phone disconnects "
                f"for a few seconds and reconnects by itself, then {T} introduces itself.{back}" if lang == "en" else
                f"✓ {T} 检查通过。已安排切换：这一轮结束后 Agent J 重启并换成 {T}，手机会断开几秒再自动连上，然后 {T} 会先自报家门。{back}"
               ) + r.get("note", "")
    if r.get("pending"):
        return ("… The check of {t} is still running; see the outcome with `agentj agent switch --result` in a minute.".format(t=T)
                if lang == "en" else f"… {T} 还在检查中，一分钟后用 `agentj agent switch --result` 看结果。")
    if r.get("reason") == "no_way_back":        # P282: the target works, the way back does not
        why = reason_text(r.get("back") or "call_failed", r.get("from") or "", lang)
        return (f"✗ Not switched to {T}: it works, but afterwards there would be no way back to {F}, which fails the same check "
                f"in the background service. Why: {why}. Fix {F} first; to switch anyway add --force (agentj agent switch --undo "
                f"then goes back with no check). Nothing was changed." if lang == "en" else
                f"✗ 没有切到 {T}：{T} 能用，但切过去以后就切不回 {F} 了，{F} 在后台服务里过不了同样的检查。原因：{why}。"
                f"先把 {F} 修好；确定要切就加 --force，之后用 agentj agent switch --undo 不经检查直接切回。没有改任何东西。")
    why = reason_text(r.get("reason") or "call_failed", r.get("target") or "", lang, None, r.get("err"), r.get("line"))
    undo = ((" It is the harness used before the last switch: agentj agent switch --undo goes back to it with no check "
             "(if it does not come up, Agent J returns here by itself).") if lang == "en" else
            "这是上次切走前用的那个：agentj agent switch --undo 可以不经检查直接切回（起不来会自动回到现在这个）。"
            ) if r.get("undo_possible") else ""
    if not T:
        return f"✗ {why}." if lang == "en" else f"✗ {why}。"
    return (f"✗ Cannot switch to {T}: {why}. Nothing was changed.{undo}" if lang == "en" else
            f"✗ 不能切换到 {T}：{why}。没有改任何东西。{undo}")


def _say_receipt(rec: dict | None, lang: str) -> str:
    if rec and rec.get("ark_plan_fix"):
        from .ark_plan_fix import guidance
        return _say_receipt({k:v for k,v in rec.items() if k != "ark_plan_fix"}, lang) + "\n" + guidance(rec["ark_plan_fix"], lang)
    if not rec:
        return "No switch yet." if lang == "en" else "还没有切换过。"
    T, F = LABEL.get(rec.get("target"), rec.get("target")), LABEL.get(rec.get("from") or "", rec.get("from") or "—")
    when = time.strftime("%m-%d %H:%M", time.localtime(rec["at"]))
    st = rec.get("status")
    if st == "ok":
        model = rec.get("model") or ""
        plan = "Agent Plan" if model.startswith("ark-agent-plan/") else "Coding Plan" if model.startswith("ark-coding-plan/") else None
        return f"{when} ✓ {F} → {T}" + ((f" · Now using Volcano {plan}" if lang == "en" else f" · 已改用火山 {plan}") if plan else "")
    waited = rec.get("waited")
    secs = ((f" (waited {waited} s)" if lang == "en" else f"（等了 {waited} 秒）") if isinstance(waited, int) else "") + (
        (f" · session start measured before the switch: {rec['start_secs']} s" if lang == "en" else
         f" · 切换前实测会话启动 {rec['start_secs']} 秒") if isinstance(rec.get("start_secs"), int) else "")
    if st == "rolled_back":
        return f"{when} ✗ {F} → {T}: " + reason_text(rec.get("reason") or "", rec.get("target") or "", lang, rec.get("detail"),
                                                     rec.get("err")) + (f"; back on {F}" if lang == "en" else f"；已切回 {F}") + secs
    if st == "probe_ok":
        return f"{when} ✓ {T}: " + ("passed the check (dry run)" if lang == "en" else "检查通过（只检查，没切换）")
    if st == "failed":
        return f"{when} ✗ {F} → {T}: " + reason_text(rec.get("reason") or "", rec.get("target") or "", lang, None,
                                                     rec.get("err"), rec.get("line")) + secs
    return f"{when} … {F} → {T}: {st}"


def cli(a, st, lang: str) -> int:
    if getattr(a, "result", False):
        rec = read_receipt(st)
        print(json.dumps(rec or {}, ensure_ascii=False) if a.json else _say_receipt(rec, lang))
        return 0
    if getattr(a, "worker", False):
        return _job(a, st)
    if getattr(a, "scan", False):
        r = scan(st)
        print(json.dumps(r, ensure_ascii=False) if a.json else _say_scan(r, lang))
        return 0 if r.get("ok") else 4 if r.get("pending") else 3
    target = getattr(a, "target", None)
    if getattr(a, "undo", False):
        r = request(st, None, undo=True)
        print(json.dumps(r, ensure_ascii=False) if a.json else _say(r, lang))
        return 0 if r.get("ok") else 4 if r.get("pending") else 3
    if getattr(a, "preset", None) == "ark" and target == "opencode":    # P371: 「OpenCode · 火山方舟」
        from . import ark
        r = ark.switch(st, getattr(a, "plan", None) or ark.DEFAULT_PLAN, a.model, dry_run=getattr(a, "dry_run", False))
        print(json.dumps(r, ensure_ascii=False) if a.json else ark.say_switch(r, lang))
        return 0 if r.get("ok") else 4 if r.get("pending") else 3
    if target not in KINDS:
        print("agentj agent switch <claude|codex|opencode> [--model M] [--dry-run] [--force] [--json] · agentj agent switch opencode --preset ark [--plan coding|payg] · --scan · --undo · --result",
              file=sys.stderr)
        return 2
    r = request(st, target, a.model, dry_run=getattr(a, "dry_run", False), force=getattr(a, "force", False))
    print(json.dumps(r, ensure_ascii=False) if a.json else _say(r, lang))
    return 0 if r.get("ok") else 4 if r.get("pending") else 3


def _job(a, st) -> int:
    """The job side (`--worker`): the service's environment, then (P282) a stale replay does nothing, saved harness paths a
    self-update left behind are healed, and on macOS the one-shot launchd job unloads itself last."""
    from .service_recovery import cleanup_job
    try:
        if stale_job(st, getattr(a, "job", None)):
            st.log("harness_switch", status="stale_job")
            return 0
        service_environment()
        try:
            healed = service.heal_binary_pins(service.name())
            if healed:
                st.log("harness_pin_healed", kinds=[h["kind"] for h in healed])
        except Exception:  # noqa: BLE001 — healing is a convenience; resolve() already bypasses a stale path
            pass
        if getattr(a, "scan", False):
            return scan_worker(st, getattr(a, "job", None))
        return worker(st, getattr(a, "target", None), a.model, job=getattr(a, "job", None),
                      dry_run=getattr(a, "dry_run", False), force=getattr(a, "force", False), undo=getattr(a, "undo", False))
    finally:
        cleanup_job(st)
