"""Fixed setup schema, bilingual errors and recommendation planning.

Signed owner dispatch is in install_assist, finite effect stages in setup_runtime.
Native execution reuses fixed Host adapters; Cloud installer authority remains
unavailable. Catalog flags describe signature requirements, never eligibility.
Historical schema source: b9b722f4d328cd29f582eee7d10c15130a848e51.
"""
from __future__ import annotations
import json
import re

CLIS = ("claude", "codex", "opencode")


SOURCES = ("official", "mirror")


PROVIDERS = ("agentsrelay", "anthropic", "openai")


ISSUES = ("linger", "isolation")


LOGIN_CLIS = ("claude", "codex")


_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/\[\]@+-]{0,99}")


_OC_MODEL = re.compile(r"[A-Za-z0-9._-]+/[^\s/][^\s]{0,98}")


MAX_PARAMS = 512                     # bytes of canonical JSON params


RELAY_MODEL = "claude-opus-5-5"      # design §七: Agents Relay → Opus 5.5 by default


def _enum(*values):
    return lambda v: isinstance(v, str) and v in values


def _model(v):
    return isinstance(v, str) and bool(_MODEL.fullmatch(v) or _OC_MODEL.fullmatch(v))


OPS = {
    "sys.check": (False, {}),
    "ai.plan": (False, {}),
    "ai.install": (True, {"cli": (_enum(*CLIS), True), "source": (_enum(*SOURCES), False)}),
    "ai.key": (True, {"provider": (_enum(*PROVIDERS), True)}),
    "ai.login": (True, {"cli": (_enum(*LOGIN_CLIS), True)}),
    "ai.probe": (True, {"cli": (_enum(*CLIS), True), "model": (_model, False)}),
    "ai.connect": (True, {"cli": (_enum(*CLIS), True), "model": (_model, False)}),
    "browser.setup": (True, {}),
    "sys.repair_hint": (True, {"issue": (_enum(*ISSUES), True)}),
}


READ_ONLY = tuple(k for k, v in OPS.items() if not v[0])


HINT = {"cli": list(CLIS), "source": list(SOURCES), "provider": list(PROVIDERS), "issue": list(ISSUES), "model": "model id"}


def catalog() -> dict:
    """What a remote needs to build the buttons: per op, signed or not and its parameters (names, allowed values)."""
    out = {}
    for op, (signed, schema) in OPS.items():
        out[op] = {"signed": signed, "params": {k: {"required": req, "values": HINT[k] if k != "model" else None}
                                                for k, (_, req) in schema.items()}}
    return out


class OpError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(code)
        self.code, self.detail = code, detail


def canonical(params: dict) -> str:
    return json.dumps(params, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def validate(op, params) -> dict:
    """The params exactly as sent, checked against the op's schema. Raises OpError(unknown_op | shape | unknown_field |
    bad_param | missing_param). There is no field that takes a command, a path or a URL — anything else is refused."""
    if not isinstance(op, str) or op not in OPS:
        raise OpError("unknown_op")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise OpError("shape")
    try:
        if len(canonical(params).encode()) > MAX_PARAMS:
            raise OpError("shape")
    except (TypeError, ValueError):
        raise OpError("shape") from None
    schema = OPS[op][1]
    for k in params:
        if k not in schema:
            raise OpError("unknown_field", str(k)[:40])
    for k, (ok, req) in schema.items():
        if k not in params:
            if req:
                raise OpError("missing_param", k)
            continue
        if not ok(params[k]):
            raise OpError("bad_param", k)
    if op in ("ai.probe", "ai.connect") and params.get("model"):
        m = params["model"]
        if params["cli"] == "opencode" and not _OC_MODEL.fullmatch(m):
            raise OpError("bad_param", "model")
        if params["cli"] != "opencode" and not _MODEL.fullmatch(m):
            raise OpError("bad_param", "model")
    return dict(params)


ERRORS = {
    "unavailable": ("当前缺少合格执行后端，请主人使用本机准备清单。", "A qualified execution backend is unavailable; use the local owner checklist."),
    "cloud_qualification_unavailable": ("安装助手资格验证暂不可用，请主人在本机继续。", "Installer eligibility verification is unavailable; continue locally as owner."),
    "unknown_op": ("这个操作不存在。", "There is no such operation."),
    "shape": ("请求格式不对，请刷新页面再试。", "The request was malformed; reload the page and try again."),
    "unknown_field": ("请求里有不认识的内容，已拒绝。", "The request carried something unknown and was refused."),
    "bad_param": ("参数不对（只能从卡片上的选项里选）。", "A parameter was not one of the card's choices."),
    "missing_param": ("少了一个必选项。", "A required choice is missing."),
    "not_for_item": ("这一项不能做这个操作。", "This step does not offer that operation."),
    "stale": ("手机时间和电脑差太多，或请求太旧：校准手机时间后再试。",
              "The phone's clock is off or the request is too old: fix the phone time and try again."),
    "replay": ("同一个请求已经处理过了。", "This request was already handled."),
    "bad_signature": ("签名不对，没执行。", "The signature did not match; nothing ran."),
    "no_key": ("这台遥控器没有批准钥匙，重新配对后再试。", "This remote has no approval key; pair it again."),
    "changed": ("清单刚更新过，页面已刷新，请再点一次。", "The checklist just changed; the page refreshed — tap again."),
    "busy": ("上一个操作还在进行，等它完成。", "Another operation is still running; wait for it."),
    "stopped": ("Agent J 正处于急停状态，先恢复再操作。", "Agent J is emergency-stopped; resume it first."),
    "network": ("网络不通或被挡住了：换成「国内镜像」再试，或检查代理。",
                "The network failed or was blocked: try the mirror source or check the proxy."),
    "checksum": ("下载的文件校验不对，已丢弃，什么都没装。过几分钟再试。",
                 "The downloaded file failed its checksum and was discarded; nothing was installed. Try again later."),
    "node_missing": ("这台电脑缺 Node.js，自动安装也没成功。", "Node.js is missing here and could not be installed automatically."),
    "install_failed": ("安装没成功（npm 报错或超时）。换个下载来源再试。", "The installation failed (npm error or timeout). Try the other source."),
    "not_installed": ("这个 AI 工具还没安装，先点「安装」。", "This AI tool is not installed yet; tap Install first."),
    "no_device": ("没有能批准的遥控器（需要已配对、带批准钥匙）。", "No remote that can approve (paired, with an approval key)."),
    "timeout": ("时间到了还没完成，可以再试一次。", "It did not finish in time; you can try again."),
    "denied": ("在卡片上点了拒绝，什么都没改。", "The card was declined; nothing changed."),
    "key_failed": ("密钥没保存成功。", "The key was not saved."),
    "login_failed": ("登录没完成：没登录上，或者授权码不对。", "Sign-in did not complete: not signed in, or a wrong code."),
    "login_url": ("登录程序没给出可用的网址。", "The sign-in program did not give a usable address."),
    "connect_failed": ("没能接上这个 AI，已退回原来的状态。", "Could not connect this AI; things were put back as they were."),
    "no_service": ("Agent J 没有作为后台服务运行，接上 AI 需要它自动重启。先在电脑上运行 agentj service install。",
                   "Agent J is not running as a background service (connecting needs it to restart): run agentj service install."),
    "same": ("现在接的就是它。", "It is already the connected AI."),
    "probe_pending": ("检查还在进行，过一会儿看结果。", "The check is still running; look again shortly."),
    "unsupported_platform": ("这台电脑的系统不支持这个操作。", "This operation is not supported on this computer's system."),
    "io": ("电脑上读写文件出错。", "A file on the computer could not be read or written."),
    "error": ("出了意外错误，已记录（不含任何密钥）。", "An unexpected error was recorded (no secrets)."),
    "native_timeout": ("60 秒内没回话：多半是没登录、key 不对，或网络 / 代理不通。",
                       "No reply within 60 s: usually not signed in, a wrong key, or no network / proxy."),
    "native_failed": ("没能完成一次真实调用：多半是没登录或 key 失效。", "No real call completed: usually not signed in or an expired key."),
    "call_failed": ("没能完成一次真实调用：多半是没登录或 key 失效。", "No real call completed: usually not signed in or an expired key."),
    "empty_probe_reply": ("回了空话。", "It replied with nothing."),
}


def explain(code: str) -> dict:
    zh, en = ERRORS.get(code, ERRORS["error"])
    return {"zh": zh, "en": en}


def plan(check: dict) -> dict:
    """The recommendation from a sys.check: reuse a CLI that is already signed in; else official (401 reachable) or Agents
    Relay (403 / 000 — Hong Kong, mainland China). → {route, cli, model?, reason{zh,en}, steps:[{op, params}]}."""
    reg = check.get("region") or {}
    a, o = reg.get("anthropic"), reg.get("openai")
    signed = [c["name"] for c in check.get("clis", []) if c["installed"] and c.get("logged_in")]
    if signed:
        cli = "claude" if "claude" in signed else signed[0]
        steps = [{"op": "ai.probe", "params": {"cli": cli}}, {"op": "ai.connect", "params": {"cli": cli}}]
        return {"route": "reuse", "cli": cli, "steps": steps,
                "reason": {"zh": "这台电脑上已经有登录好的 AI 工具，直接试一下、接上。",
                           "en": "An AI tool here is already signed in: try it and connect it."}}
    installed = {c["name"] for c in check.get("clis", []) if c["installed"]}
    if a == "401" or o == "401":
        cli = "claude" if a == "401" else "codex"
        steps = ([] if cli in installed else [{"op": "ai.install", "params": {"cli": cli, "source": "official"}}]) + [
            {"op": "ai.login", "params": {"cli": cli}}, {"op": "ai.probe", "params": {"cli": cli}},
            {"op": "ai.connect", "params": {"cli": cli}}]
        return {"route": "official", "cli": cli, "steps": steps,
                "reason": {"zh": "官方服务网络可达：仍须主人登录并通过真实回复检查。",
                           "en": "The official endpoint is reachable: the owner must still sign in and verify a model reply."}}
    steps = ([] if "claude" in installed else [{"op": "ai.install", "params": {"cli": "claude", "source": "mirror"}}]) + [
        {"op": "ai.key", "params": {"provider": "agentsrelay"}},
        {"op": "ai.probe", "params": {"cli": "claude", "model": RELAY_MODEL}},
        {"op": "ai.connect", "params": {"cli": "claude", "model": RELAY_MODEL}}]
    return {"route": "relay", "cli": "claude", "model": RELAY_MODEL, "steps": steps,
            "reason": {"zh": "官方服务连不上或不支持所在地区（香港、大陆常见）：推荐 Agents Relay 中转，先在 agentsrelay.net 买好套餐拿到 key。",
                       "en": "The official services are unreachable or not offered here (common in Hong Kong / mainland China): "
                             "Agents Relay is recommended — buy a plan at agentsrelay.net to get a key."}}
