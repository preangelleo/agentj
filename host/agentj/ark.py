"""Agent J ark; developer reference: documentation/notes/P0262-HOST-REFERENCE.md."""
from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import shlex

from . import opencode_provider as op

DATA = pathlib.Path(__file__).with_name("ark.json")
PROFILE = "ark-claude"
PROFILE_AGENT = "ark-agent-claude"     # P395

TERMS = {   # P348 (2026-10-16): the owner's own tasks may use the plan; support bots never (bots/provider.py)
    "zh": "火山方舟 Coding Plan 是包月套餐（条款以方舟控制台为准）：你自己的定时任务和后台 Agent 可以用它；长时间连续跑的后台任务服务商"
          "可能限流，可改用按量付费的方舟 API Key。客服 bot 是在服务别人，Agent J 不会把 Coding Plan 的 Key 交给 bot，要另填一把按量 Key。"
          "Coding Plan 不含生图模型。",
    "en": "Volcano Ark Coding Plan is a monthly plan (Ark's console terms are authoritative): your own scheduled tasks and "
          "background Agents may use it; the provider may rate-limit long continuous background work, so you can switch those "
          "to a pay-as-you-go Ark key. A support bot serves other people, so Agent J never gives it the Coding Plan key — add a "
          "pay-as-you-go key for the bot. The Coding Plan has no image-generation models.",
}
# P328: Lite and Pro have the same models (Kimi K3 included); Ark only *suggests* Pro for K3 because it uses quota fast
PRO_NOTE = {"zh": "（kimi-k3 扣额度快，方舟建议 Pro 套餐用；Lite 也能选）",
            "en": " (kimi-k3 uses quota fast: Ark suggests it for Pro, but Lite can pick it too)"}


PLANS = ("agent", "coding", "payg")
DEFAULT_PLAN = "agent"     # P395: what Ark sells now
TERMS_AGENT = {   # P395
    "zh": "火山方舟 Agent Plan 是包月套餐（条款以方舟控制台为准）：你自己的定时任务和后台 Agent 可以用它；客服 bot 是在服务别人，Agent J 不会把"
          " Agent Plan 的 Key 交给 bot，要另填一把按量 Key。别把套餐地址换成 …/api/v3（那是按量计费，会额外扣钱）。",
    "en": "Volcano Ark Agent Plan is a monthly plan (Ark's console terms are authoritative): your own scheduled tasks and "
          "background Agents may use it; a support bot serves other people, so Agent J never gives it the Agent Plan key — add "
          "a pay-as-you-go key for the bot. Never point the plan at …/api/v3 (billed extra, per token).",
}
KEY_HINT = {   # P395: where the Agent Plan key is — it is NOT one from API Key management (that one gets 401 here)
    "zh": "方舟控制台 订阅 → Agent Plan → 使用配置 → 专属 API Key（不是「API Key 管理」里的那种）",
    "en": "Ark console: Subscription → Agent Plan → Usage configuration → the plan's own API key (not one from API Key management)",
}
MEDIUM_NOTE = {"zh": "（kimi-k3 从 Medium 套餐起才有）", "en": " (kimi-k3 needs Medium or above)"}
PAYG_TERMS = {   # P371
    "zh": "按量付费走方舟标准地址 …/api/v3：按 token 从账户余额扣费，任何程序都能调用（不占套餐额度）。同一把方舟 API Key 两个地址都能用，"
          "是地址决定扣套餐还是扣余额。",
    "en": "Pay-as-you-go uses Ark's standard address …/api/v3: billed per token from the account balance, any program may call "
          "it (no plan quota). One Ark API key works on both addresses — the address decides whether the plan or the balance pays.",
}
PLAN_ONLY = {   # P371: said wherever the plan address is offered (Ark's terms)
    "zh": "套餐地址（…/api/coding）只给编程 Agent 用（OpenCode、Claude Code 等）；别的程序拿它调用会被方舟判为滥用、可能封号。",
    "en": "The plan address (…/api/coding) is for coding Agents only (OpenCode, Claude Code …); any other program calling it "
          "may be treated by Ark as abuse and the account banned.",
}
_PAYG_ID = re.compile(r"[a-z0-9][a-z0-9.-]{2,80}")


def data() -> dict:
    return json.loads(DATA.read_text(encoding="utf-8"))


def plan_data(plan: str = "coding") -> dict:
    """The preset for one way in: the Coding Plan (top level of ark.json), the Agent Plan (`agent`: the Coding Plan's models
    minus the ones it refuses, on its own addresses and key) or pay-as-you-go (`payg`)."""
    d = data()
    if plan == "payg":
        return {**d["payg"], "console_url": d["console_url"], "pro_suggested": []}
    if plan == "agent":
        a = d["agent"]
        models = {k: v for k, v in d["block"]["models"].items() if k not in a["unsupported"]}
        block = {"npm": d["block"]["npm"], "name": a["name"],
                 "options": {"baseURL": a["base_url"], "apiKey": "{env:" + a["key_env"] + "}"}, "models": models}
        return {**d, **{k: v for k, v in a.items() if k != "unsupported"}, "verify_url": None, "pro_suggested": [],
                "signup_url": d["console_url"], "block": block}
    return d


def plan_of(pid: str | None) -> str | None:
    """Which way in an OpenCode provider id is (None: not Ark's)."""
    d = data()
    return {d["provider"]: "coding", d["agent"]["provider"]: "agent", d["payg"]["provider"]: "payg"}.get(pid or "")


def vision(model: str) -> bool:
    m = data()["block"]["models"].get(model) or {}
    return "image" in (m.get("modalities") or {}).get("input", [])


def verify_for(pid: str | None) -> tuple[str, str] | None:
    """Same contract as bailian.verify_for: ("", "") = an Ark provider without a read-only check; None = not Ark."""
    d = data()
    if pid == d["payg"]["provider"]:      # P371: pay-as-you-go has a read-only check (GET …/api/v3/models)
        return d["payg"]["verify_url"], op.HEADER["openai"]
    if pid == d["agent"]["provider"]:     # P395: the Agent Plan has no read-only check either
        return ("", "")
    if pid != d["provider"]:
        return None
    return (d["verify_url"], op.HEADER["openai"]) if d.get("verify_url") else ("", "")


def _model(model: str | None, plan: str = "coding") -> str:
    d = plan_data(plan)
    if model and "/" in model and model.split("/", 1)[0] == d["provider"]:
        model = model.split("/", 1)[1]
    model = model or d["model"]
    if model in d["block"]["models"]:
        return model
    if plan == "payg" and _PAYG_ID.fullmatch(model):   # P371: any model id the account has opened (text-only unless listed)
        return model
    if plan == "payg":
        raise op.ProviderError(f"方舟模型 id 写成 doubao-seed-2-1-pro-260915 这样（控制台「模型广场」可查）：{model} 不对")
    raise op.ProviderError(f"{d['block']['name']} 没有模型 {model}（可选：{', '.join(sorted(d['block']['models']))}）")


def install(model: str | None = None, dry_run: bool = False, environ=None, plan: str = "coding") -> dict:
    """Add the preset's block to OpenCode's config unless a provider with the same id is there (then: kept untouched — P371:
    except that a pay-as-you-go model it lacks is added on its own, text-only)."""
    d = plan_data(plan)
    pid, model = d["provider"], _model(model, plan)
    path = op.config_file(environ)
    cfg = op._load(path)
    prov = cfg.get("provider")
    if prov is not None and not isinstance(prov, dict):
        raise op.ProviderError(f"{path} 里的 provider 不是对象：没有改它")
    kept = isinstance(prov, dict) and pid in prov
    block = json.loads(json.dumps(d["block"]))
    added_model = False
    if model not in block["models"]:
        block["models"][model] = {"name": model, "modalities": {"input": ["text"], "output": ["text"]}}
    if not kept:
        cfg["provider"] = {**(prov or {}), pid: block}
    elif plan == "payg" and isinstance(prov[pid], dict) and model not in (prov[pid].get("models") or {}):
        mine = prov[pid].get("models") if isinstance(prov[pid].get("models"), dict) else {}
        cfg["provider"] = {**prov, pid: {**prov[pid], "models": {**mine, model: block["models"][model]}}}
        added_model = True
    if (not kept or added_model) and not dry_run:
        op._write(path, cfg)
    models = sorted(block["models"])
    see = lambda m: "image" in ((block["models"].get(m) or {}).get("modalities") or {}).get("input", [])   # noqa: E731
    return {"file": path, "harness": "opencode", "provider": pid, "plan": plan, "kept": kept,
            "written": (not kept or added_model) and not dry_run, "added_model": added_model,
            "model": f"{pid}/{model}", "vision": see(model), "key_env": d["key_env"],
            "models": [f"{pid}/{m}" for m in models], "vision_models": [f"{pid}/{m}" for m in models if see(m)],
            "pro_suggested": d["pro_suggested"], "next": steps(model, plan)}


def card(plan: str = "coding") -> str:
    """The phone key card's command for one way in (pay-as-you-go: with Ark's read-only model list as the check)."""
    from . import service
    d = plan_data(plan)
    env_file = service.env_file(service.name())
    what = {"payg": "火山方舟 按量付费的 API Key（OpenCode 用）", "agent": "火山方舟 Agent Plan 专属 API Key（OpenCode 用；" + KEY_HINT["zh"] + "）"}.get(
        plan, "火山方舟 Coding Plan 的 API Key（OpenCode 用）")
    c = f"agentj secret request --name {d['key_env']} --purpose '{what}' --dest {op.secret_dest(env_file, d['key_env'])}"
    if d.get("verify_url"):
        c += f" --verify-url {d['verify_url']} --verify-header '{op.HEADER['openai']}'"
    return c


def steps(model: str | None = None, plan: str = "coding") -> list[str]:
    d = plan_data(plan)
    tail = f" --preset ark --plan payg --model {_model(model, plan)}" if plan == "payg" else \
        f" --model {d['provider']}/{_model(model, plan)}"
    return [card(plan), "agentj service restart", "agentj agent switch opencode" + tail]


def claude_steps(model: str | None = None, plan: str = "coding") -> list[str]:
    """Claude Code on Ark: a provider profile — every tier model and the subagent model point at the same Ark model."""
    model = _model(model, plan)
    if plan == "agent":     # P395: the Agent Plan's Anthropic-compatible address (…/api/plan), its own key
        return [f"agentj provider profile save {PROFILE_AGENT} --harness claude --preset ark-agent --model {model}",
                f"agentj secret request --name ARK_AGENT_CLAUDE_API_KEY --purpose '火山方舟 Agent Plan 专属 API Key（Claude Code 用；"
                f"{KEY_HINT['zh']}）' --dest <上一步打印的 key_destination>",
                f"agentj provider profile use {PROFILE_AGENT}"]
    return [f"agentj provider profile save {PROFILE} --harness claude --preset ark-coding --model {model}",
            f"agentj secret request --name ARK_CLAUDE_API_KEY --purpose '火山方舟 Coding Plan 的 API Key（Claude Code 用）' "
            f"--dest <上一步打印的 key_destination>",
            f"agentj provider profile use {PROFILE}"]


def facts(environ=None) -> list[dict]:
    """For doctor: the Ark provider in OpenCode's config — the key's NAME and whether the service has it (never the value)."""
    from . import provider_runtime
    try:
        listed = op.listing(environ)
    except op.ProviderError:
        return []
    try:
        fresh = provider_runtime.fresh_environment({})
    except Exception:  # noqa: BLE001 — no service: nothing known
        fresh = {}
    e = os.environ if environ is None else environ
    pids = (data()["provider"], data()["agent"]["provider"])     # P395: the Agent Plan too
    return [{"provider": r["provider"], "key_env": r.get("key_env"), "inline_key": r["inline_key"],
             "key_set": bool(r.get("key_env") and (fresh.get(r["key_env"]) or e.get(r["key_env"])))}
            for r in listed if r["provider"] in pids]


def model_note(model: str | None, lang: str = "zh") -> str | None:
    """A one-line note when the selected OpenCode model is the Coding Plan's (terms + whether it reads images)."""
    d = data()
    pid, _, m = (model or "").partition("/")
    if pid == d["payg"]["provider"]:      # P371: pay-as-you-go — billed per token, no plan terms
        g = d["payg"]
        see = "image" in (((g["block"]["models"].get(m) or {}).get("modalities") or {}).get("input", []))
        return PAYG_TERMS[lang] + ("" if see else (f"（{m} 不能看图：要看截图时换 {pid}/{g['model']}）" if lang == "zh" else
                                                   f" ({m} cannot read images: for screenshots use {pid}/{g['model']})"))
    if pid == d["agent"]["provider"]:     # P395: the Agent Plan — its terms, image input, Kimi K3 from Medium up
        a = plan_data("agent")
        eye = ("" if vision(m) else
               f"（{m} 不能看图：截图、浏览器自动化要看图时换 {pid}/{a['model']}）" if lang == "zh" else
               f" ({m} cannot read images: for screenshots / browser work use {pid}/{a['model']})")
        return TERMS_AGENT[lang] + eye + (MEDIUM_NOTE[lang] if m in a["medium_up"] else "")
    if pid != d["provider"]:
        return None
    eye = ("" if vision(m) else
           f"（{m} 不能看图：截图、浏览器自动化要看图时换 {pid}/{d['model']} 或 {pid}/{d['model_strong']}）" if lang == "zh" else
           f" ({m} cannot read images: for screenshots / browser work use {pid}/{d['model']} or {pid}/{d['model_strong']})")
    pro = (PRO_NOTE[lang] if m in d["pro_suggested"] else "")
    return TERMS[lang] + eye + pro


def render(r: dict, lang: str = "zh") -> str:
    d = data()
    agent = r.get("plan") == "agent"      # P395
    if r["harness"] == "claude":
        addr = "…/api/plan" if agent else "…/api/coding"
        terms = (TERMS_AGENT if agent else TERMS)
        if lang == "zh":
            key = f"第 2 步手机弹出密钥卡，主人只把 {KEY_HINT['zh']} 贴进卡片" if agent else "第 2 步手机弹出密钥卡，主人只把方舟控制台的 API Key 贴进卡片"
            return "\n".join([f"Claude Code 接火山方舟{' Agent Plan' if agent else ''}（Anthropic 兼容地址 {addr}）：", *(f"  {i}. {s}" for i, s in enumerate(r["next"], 1)),
                              f"  {key}；第 3 步先真实发一句测试，通过才写配置。",
                              "  HAIKU / SONNET / OPUS 默认模型和子 agent 模型都会指向同一个方舟模型（另指：--haiku-model 等）。",
                              "⚠ " + terms["zh"]])
        return "\n".join([f"Claude Code on Volcano Ark{' Agent Plan' if agent else ''} (Anthropic-compatible {addr}):", *(f"  {i}. {s}" for i, s in enumerate(r["next"], 1)),
                          "  Step 2 opens a key card on the phone" + (f" ({KEY_HINT['en']})" if agent else "") +
                          "; step 3 sends one real test message before writing anything.",
                          "  The HAIKU / SONNET / OPUS defaults and the subagent model all point at the same Ark model (others: --haiku-model …).",
                          "⚠ " + terms["en"]])
    eyes = ", ".join(m.split("/", 1)[1] for m in r["vision_models"])
    if r.get("plan") == "payg":       # P371
        return render_payg(r, eyes, lang)
    if agent:                         # P395
        return render_agent(r, eyes, lang)
    if lang == "zh":
        head = (f"{r['file']} 里已经有 {r['provider']}：保持你原来的配置，没有改。" if r["kept"] else
                ("已写入" if r["written"] else "（预演，未写入）") + f" {r['file']}：服务商 {r['provider']}（{len(r['models'])} 个模型），"
                f"key 从环境变量 {r['key_env']} 读（这里不存 key）。")
        return "\n".join([head, f"能看图的模型：{eyes}；glm-5.3、deepseek-v4-pro 等是纯文本。{PRO_NOTE['zh']}", "接下来：",
                          f"  1. {r.get('key_card_message', r['next'][0])}\n     （手机上弹出密钥卡，主人只把方舟控制台 {d['console_url']} 的 API Key 贴进卡片；Agent 看不到值）",
                          f"  2. {r['next'][1]}", f"  3. {r['next'][2]}" + ("（这个模型能看图）" if r["vision"] else ""),
                          f"想用 Claude Code：`agentj provider ark --harness claude`。", "⚠ " + TERMS["zh"]])
    head = (f"{r['provider']} already exists in {r['file']}: your own configuration is kept unchanged." if r["kept"] else
            ("Written" if r["written"] else "(dry run, nothing written)") + f" {r['file']}: provider {r['provider']} "
            f"({len(r['models'])} models), key read from {r['key_env']} (no key stored here).")
    return "\n".join([head, f"Models that read images: {eyes}; glm-5.3, deepseek-v4-pro and others are text only.{PRO_NOTE['en']}",
                      "Next:", f"  1. {r.get('key_card_message', r['next'][0])}\n     (a key card opens on the phone; the owner pastes the Ark API key there only)",
                      f"  2. {r['next'][1]}", f"  3. {r['next'][2]}" + (" (this model reads images)" if r["vision"] else ""),
                      "For Claude Code: `agentj provider ark --harness claude`.", "⚠ " + TERMS["en"]])


def render_agent(r: dict, eyes: str, lang: str) -> str:
    """P395: the Agent Plan block in OpenCode — where its key is, which tier has Kimi K3."""
    a = plan_data("agent")
    tiers = " · ".join(f"{n} ¥{c}" for n, c in a["tiers"])
    if lang == "zh":
        head = (f"{r['file']} 里已经有 {r['provider']}：保持你原来的配置，没有改。" if r["kept"] else
                ("已写入" if r["written"] else "（预演，未写入）") + f" {r['file']}：服务商 {r['provider']}（方舟 Agent Plan …/api/plan/v3，"
                f"{len(r['models'])} 个模型），key 从环境变量 {r['key_env']} 读（这里不存 key）。")
        return "\n".join([head, f"能看图的模型：{eyes}；glm-5.3、deepseek-v4-pro 等是纯文本。{MEDIUM_NOTE['zh']} 套餐（月）：{tiers}。", "接下来：",
                          f"  1. {r.get('key_card_message', r['next'][0])}\n     （手机上弹出密钥卡，主人只把 {KEY_HINT['zh']} 贴进卡片；Agent 看不到值）",
                          f"  2. {r['next'][1]}", f"  3. {r['next'][2]}" + ("（这个模型能看图）" if r["vision"] else ""),
                          "还在用旧的 Coding Plan：`agentj provider ark --plan coding`。想用 Claude Code：`agentj provider ark --harness claude`。",
                          "⚠ " + TERMS_AGENT["zh"]])
    head = (f"{r['provider']} already exists in {r['file']}: your own configuration is kept unchanged." if r["kept"] else
            ("Written" if r["written"] else "(dry run, nothing written)") + f" {r['file']}: provider {r['provider']} "
            f"(Ark Agent Plan …/api/plan/v3, {len(r['models'])} models), key read from {r['key_env']} (no key stored here).")
    return "\n".join([head, f"Models that read images: {eyes}; glm-5.3, deepseek-v4-pro and others are text only.{MEDIUM_NOTE['en']} "
                      f"Plans (monthly, CNY): {tiers}.", "Next:",
                      f"  1. {r.get('key_card_message', r['next'][0])}\n     (a key card opens on the phone; the owner pastes {KEY_HINT['en']} there only)",
                      f"  2. {r['next'][1]}", f"  3. {r['next'][2]}" + (" (this model reads images)" if r["vision"] else ""),
                      "Still on the older Coding Plan: `agentj provider ark --plan coding`. For Claude Code: `agentj provider ark --harness claude`.",
                      "⚠ " + TERMS_AGENT["en"]])


def render_payg(r: dict, eyes: str, lang: str) -> str:
    if lang == "zh":
        head = (f"{r['file']} 里已经有 {r['provider']}：保持你原来的配置" + ("，只补上了这个模型。" if r["added_model"] else "，没有改。")
                if r["kept"] else ("已写入" if r["written"] else "（预演，未写入）") +
                f" {r['file']}：服务商 {r['provider']}（方舟按量付费 …/api/v3，{len(r['models'])} 个模型），key 从环境变量 {r['key_env']} 读（这里不存 key）。")
        return "\n".join([head, f"能看图的模型：{eyes}；deepseek、glm 是纯文本。其他已开通的模型：--model <模型 id>。", "接下来：",
                          f"  1. {r.get('key_card_message', r['next'][0])}\n     （手机上弹出密钥卡，主人只把方舟控制台的 API Key 贴进卡片；卡片会用方舟的模型列表核对一次，Agent 看不到值）",
                          f"  2. {r['next'][1]}", f"  3. {r['next'][2]}（先真实调用一次，通不了自动切回）", "ℹ " + PAYG_TERMS["zh"]])
    head = (f"{r['provider']} already exists in {r['file']}: kept" + (", only this model was added." if r["added_model"] else " unchanged.")
            if r["kept"] else ("Written" if r["written"] else "(dry run, nothing written)") +
            f" {r['file']}: provider {r['provider']} (Ark pay-as-you-go …/api/v3, {len(r['models'])} models), key read from {r['key_env']}.")
    return "\n".join([head, f"Models that read images: {eyes}; deepseek and glm are text only. Others you opened: --model <model id>.",
                      "Next:", f"  1. {r.get('key_card_message', r['next'][0])}\n     (a key card opens on the phone, checked once against Ark's model list)",
                      f"  2. {r['next'][1]}", f"  3. {r['next'][2]} (one real call first; on failure it switches back)", "ℹ " + PAYG_TERMS["en"]])


# ------------------------------------------------------------------ P371: 「OpenCode · 火山方舟」 as one switch
def detect(which=shutil.which, run=subprocess.run) -> dict:
    """OpenCode (needed) and Ark's own CLI (arkcli, optional): installed + version. Never logs in, never reads a key."""
    out = {}
    for name, exe in (("opencode", "opencode"), ("arkcli", "arkcli")):
        path = which(exe)
        ver = None
        if path:
            try:
                p = run([path, "--version"], capture_output=True, text=True, timeout=15)
                m = re.search(r"\d+\.\d+\.\d+", (p.stdout or "") + (p.stderr or ""))
                ver = m.group(0) if m else "?"
            except (OSError, subprocess.SubprocessError):
                ver = "?"
        out[name] = ver
    return out


def key_known(name: str, environ=None) -> bool | None:
    """True / False when the service's environment file is readable here; None inside the fence (the job checks)."""
    from . import provider_runtime
    e = os.environ if environ is None else environ
    if e.get(name):
        return True
    try:
        pathlib.Path(provider_runtime.env_path()).read_text()
    except (OSError, ValueError):
        return None
    except Exception:  # noqa: BLE001 — no service at all
        return None
    return bool(provider_runtime.fresh_environment({}, extra=(name,)).get(name))


def switch(st, plan: str = DEFAULT_PLAN, model: str | None = None, dry_run: bool = False, *, request=None, detector=None,
           key=None, environ=None) -> dict:
    """detect → key (card command when it is known to be missing) → write the block → switch_harness.request (one real call
    through OpenCode in the service's environment, switch, switch back on failure). A block added here is removed again
    when the switch fails; one that was already there stays exactly as it was."""
    if plan not in PLANS:
        return {"ok": False, "reason": "bad_plan", "preset": "ark"}
    found = (detector or detect)()
    d = plan_data(plan)
    base = {"preset": "ark", "plan": plan, "target": "opencode", "arkcli": found.get("arkcli")}
    if not found.get("opencode"):
        return {**base, "ok": False, "reason": "not_installed", "install": "npm i -g @opencode/cli"}
    try:
        model = _model(model, plan)
    except op.ProviderError as e:
        return {**base, "ok": False, "reason": "bad_model", "error": str(e)}
    has = (key or key_known)(d["key_env"], environ)
    if has is False:
        out = {**base, "ok": False, "reason": "need_key", "key_env": d["key_env"], "next": steps(model, plan)}
        op.open_key_card(out, dry_run)
        return out
    try:
        inst = install(model, environ=environ, plan=plan)
    except op.ProviderError as e:
        return {**base, "ok": False, "reason": "invalid_native_config", "error": str(e)}
    if request is None:
        from .switch_harness import request
    r = request(st, "opencode", inst["model"], dry_run=dry_run)
    out = {**r, **base, "model": inst["model"], "config": inst["file"], "written": inst["written"]}
    if r.get("reason") == "same":          # already on OpenCode: keep the block, change the model instead
        return {**out, "hint": f"agentj agent opencode --model {inst['model']}"}
    if not r.get("ok") and not r.get("pending"):
        if inst["written"] and not inst["kept"]:
            op.remove(d["provider"], environ)
            out["config_restored"] = True
        if has is None and r.get("reason") not in ("same", "busy", "too_soon"):
            out["next"] = steps(model, plan)       # inside the fence nobody could tell whether the key is there
    return out


def say_switch(r: dict, lang: str = "zh") -> str:
    from .switch_harness import _say
    zh = lang != "en"
    tool = ("arkcli " + r["arkcli"] + "（已装，仅探测：它的 helper 要在终端里 SSO 登录，Agent J 自己写等价配置）" if zh else
            "arkcli " + r["arkcli"] + " (found, detected only: its helper needs an SSO login in a terminal)") \
        if r.get("arkcli") else ("没有装 arkcli（不需要）" if zh else "arkcli not installed (not needed)")
    way = {"payg": ("按量付费 …/api/v3", "pay-as-you-go …/api/v3"), "agent": ("Agent Plan 套餐 …/api/plan/v3", "Agent Plan …/api/plan/v3")}.get(
        r.get("plan"), ("Coding Plan 套餐 …/api/coding/v3", "Coding Plan …/api/coding/v3"))[0 if zh else 1]
    head = (f"OpenCode · 火山方舟（{way}）· {tool}" if zh else f"OpenCode · Volcano Ark ({way}) · {tool}")
    reason = r.get("reason")
    if reason == "not_installed":
        body = ("还没装 OpenCode：先装 `npm i -g @opencode/cli`，再来切。" if zh else "OpenCode is not installed: `npm i -g @opencode/cli` first.")
    elif reason == "need_key":
        body = ("服务环境里还没有 " + r["key_env"] + "：先让主人在手机密钥卡里填 key（Agent 看不到值），再重新切换：\n  " if zh else
                r["key_env"] + " is not in the service's environment yet: the owner fills the key card first, then switch again:\n  ") \
            + "\n  ".join(r["next"])
        if r.get("plan") == "agent":      # P395
            body += ("\n  （key 在 " + KEY_HINT["zh"] + "）" if zh else "\n  (the key: " + KEY_HINT["en"] + ")")
    elif reason in ("bad_model", "bad_plan", "invalid_native_config"):
        body = r.get("error") or reason
    elif reason == "same":
        body = ("现在用的就是 OpenCode：方舟服务商已写好，换成方舟的模型：\n  " if zh else
                "Already on OpenCode: the Ark provider is in place; change the model:\n  ") + r["hint"] + "\n  agentj service restart"
    else:
        body = _say(r, lang)
        if r.get("config_restored"):
            body += ("\n刚写进 OpenCode 配置的方舟服务商已经撤掉，配置和切换前一样。" if zh else
                     "\nThe Ark provider just added to OpenCode's config was removed again: the config is as before.")
        if r.get("next"):
            body += ("\n如果是 key 还没填：\n  " if zh else "\nIf the key is missing:\n  ") + "\n  ".join(r["next"])
    if r.get("key_card_message"):
        body = r["key_card_message"] + "\n" + body
    terms = (PAYG_TERMS if r.get("plan") == "payg" else PLAN_ONLY)["zh" if zh else "en"]
    if r.get("plan") == "agent":          # P395: the Agent Plan's own address
        terms = terms.replace("…/api/coding", "…/api/plan")
    if r.get("ok") and r.get("plan") == "payg" and r.get("privacy"):     # model_note already said it
        return "\n".join([head, body])
    return "\n".join([head, body, ("ℹ " if r.get("plan") == "payg" else "⚠ ") + terms])


def cmd(a) -> int:
    lang = "en" if getattr(a, "lang", None) == "en" else "zh"
    model = (a.model or [None])[0]
    # P395: Agent Plan by default; `coding` / `coding-plan` keep the Coding Plan
    plan = {"payg": "payg", "coding": "coding", "coding-plan": "coding"}.get(getattr(a, "plan", None) or "", DEFAULT_PLAN)
    if getattr(a, "plan", None) == "token-plan":
        print(json.dumps({"ok": False, "error": "token-plan"}, ensure_ascii=False) if a.json else
              "没有改：方舟的 --plan 是 agent（默认）、coding 或 payg", file=sys.stdout if a.json else sys.stderr)
        return 2
    try:
        if getattr(a, "harness", None) == "claude":
            if plan == "payg":
                raise op.ProviderError("Claude Code 只接方舟的套餐地址（Anthropic 兼容）；按量付费请用 OpenCode")
            r = {"harness": "claude", "plan": plan, "model": _model(model, plan), "next": claude_steps(model, plan)}
            if not a.dry_run:
                from . import provider_profiles
                pid = PROFILE_AGENT if plan == "agent" else PROFILE
                declared = provider_profiles.define(pid, "claude", plan_data(plan)["anthropic_base"], r["model"])
                r["next"] = [step.replace("<上一步打印的 key_destination>", shlex.quote(declared["key_destination"])) for step in r["next"]]
        else:
            r = install(model, a.dry_run, plan=plan)
    except op.ProviderError as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False) if a.json else f"没有改：{e}",
              file=sys.stdout if a.json else sys.stderr)
        return 2
    terms = {"payg": PAYG_TERMS, "agent": TERMS_AGENT}.get(plan, TERMS)
    op.open_key_card(r, a.dry_run)
    print(json.dumps({"ok": True, **r, "terms": terms}, ensure_ascii=False) if a.json else render(r, lang))
    return 0
