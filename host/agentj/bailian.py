"""P310 (Leo, 2026-10-15): Alibaba Cloud Bailian (Model Studio) plans in OpenCode — `agentj provider bailian [--plan …]`.

The mainland China route next to Kimi (ai_route.py): a monthly plan instead of pay-as-you-go API prices.
- **Token Plan, personal** (`token-plan`, recommended): Lite 39 / Essential 79 / Standard 139 / Pro 499 CNY a month, metered
  in Credits; Qwen 3.8 Max / Flash with image input (screenshots, browser work), GLM, DeepSeek. Anthropic-compatible
  endpoint for OpenCode, its own key (BAILIAN_TOKEN_PLAN_API_KEY).
- **Coding Plan** (`coding-plan`, second choice): no Max model, no read-only key check (its model list answers any key).

What `bailian.json` holds is the official OpenCode block (help.aliyun.com/zh/model-studio/opencode-token-plan) with the key
as an `{env:NAME}` reference. Writing it never replaces anything the owner already has: an existing provider of the same id
is left exactly as it is (`kept`), and no top-level `model` is set — the model is chosen with `agentj agent switch opencode
--model …`. The key goes only through the phone's key card (`agentj secret request`), verified against the read-only model
list; this module never reads it.

Terms (the purchase page, 2026-10-15): calls only inside coding tools used interactively — no automation scripts, app
back ends or non-interactive batch calls with this key; inputs and outputs are used to improve the service; one subscriber,
no sharing. P348 (Leo, 2026-10-16): both plan keys may still run the owner's own scheduled tasks (fence.PLAN_KEY_ENV) —
the texts say the provider may rate-limit long background runs; support bots never get them (bots/provider.coding_plan).
"""
from __future__ import annotations

import json
import os
import pathlib
import sys

from . import opencode_provider as op

DATA = pathlib.Path(__file__).with_name("bailian.json")
PLANS = ("token-plan", "coding-plan")
LABEL = {"token-plan": "Token Plan", "coding-plan": "Coding Plan"}
VISION = ("qwen3.8-max", "qwen3.8-flash", "qwen3.7-plus", "qwen3.6-flash", "qwen3.6-plus", "qwen3.5-plus", "kimi-k2.5")

TERMS = {   # P348 (2026-10-16): the owner's own tasks may use the plan; support bots never (bots/provider.py)
    "zh": "百炼 Token Plan 是包月套餐（条款以阿里云订阅页为准）：你自己的定时任务和后台 Agent 可以用它；长时间连续跑的后台任务服务商可能限流，"
          "可改用按量付费的 Key。使用期间模型输入和输出会被阿里云用于服务改进与模型优化（涉及隐私数据请慎用）；套餐限订阅人本人，不能共享。"
          "客服 bot 是在服务别人，Agent J 不会把 Token Plan 的 Key 交给 bot，要另填一把按量 Key。",
    "en": "Bailian Token Plan is a monthly plan (the Alibaba Cloud subscription page is authoritative): your own scheduled tasks "
          "and background Agents may use it; the provider may rate-limit long continuous background work, so you can switch "
          "those to a pay-as-you-go key. While subscribed, model inputs and outputs are used by Alibaba Cloud to improve the "
          "service (be careful with private data); for the subscriber only, no sharing. A support bot serves other people, so "
          "Agent J never gives it the Token Plan key — add a pay-as-you-go key for the bot.",
}

# P348 (2026-10-16): the Coding Plan key may run the owner's own scheduled tasks (fence.PLAN_KEY_ENV); bots never
CODING_TERMS = {
    "zh": "百炼 Coding Plan 是包月套餐（条款以百炼控制台为准）：你自己的定时任务和后台 Agent 可以用它；长时间连续跑的后台任务服务商可能限流，"
          "可改用按量付费的 Key。客服 bot 是在服务别人，Agent J 不会把 Coding Plan 的 Key 交给 bot，要另填一把按量 Key。",
    "en": "Bailian Coding Plan is a monthly plan (the Bailian console's terms are authoritative): your own scheduled tasks and "
          "background Agents may use it; the provider may rate-limit long continuous background work, so you can switch those "
          "to a pay-as-you-go key. A support bot serves other people, so Agent J never gives it the Coding Plan key — add a "
          "pay-as-you-go key for the bot.",
}


def terms(plan_name: str | None, lang: str = "zh") -> str:
    return (CODING_TERMS if plan_name == "coding-plan" else TERMS)[lang]


def data() -> dict:
    return json.loads(DATA.read_text(encoding="utf-8"))["plans"]


def plan(name: str) -> dict:
    p = data().get(name)
    if p is None:
        raise op.ProviderError(f"没有这个百炼套餐：{name}（可选 {' / '.join(PLANS)}）")
    return p


def by_provider(pid: str | None) -> dict | None:
    """The plan whose OpenCode provider id is `pid` (`bailian-token-plan-personal` …), else None."""
    return next((dict(p, plan=k) for k, p in data().items() if p["provider"] == pid), None)


def verify_for(pid: str | None) -> tuple[str, str] | None:
    """(read-only verify URL, header) for the key card of a Bailian provider; ("", "") = no read-only check exists (Coding
    Plan); None = not a Bailian provider. The Anthropic-compatible base URL has no model list (404), the OpenAI one has."""
    p = by_provider(pid)
    if p is None:
        return None
    return (p["verify_url"], op.HEADER["openai"]) if p.get("verify_url") else ("", "")


def tiers_text(name: str, lang: str) -> str:
    out = []
    for t, price, credits in plan(name)["tiers"]:
        c = f"{credits:,}" if credits else ""
        out.append(f"{t} ¥{price}/月" + (f"（{c} Credits）" if c else "") if lang == "zh" else
                   f"{t} ¥{price}/month" + (f" ({c} Credits)" if c else ""))
    note = "（2026-10-15 阿里云官网价，以官网为准）" if lang == "zh" else " (Alibaba Cloud's page on 2026-10-15; theirs is authoritative)"
    return " · ".join(out) + note


def install(name: str = "token-plan", model: str | None = None, dry_run: bool = False, environ=None) -> dict:
    """Add the plan's provider block to OpenCode's config unless one with the same id is there (then: kept untouched)."""
    p = plan(name)
    pid = p["provider"]
    models = sorted(p["block"]["models"])
    model = model or p["model"]
    if model not in models:
        raise op.ProviderError(f"{pid} 没有模型 {model}（可选：{', '.join(models)}）")
    path = op.config_file(environ)
    cfg = op._load(path)
    prov = cfg.get("provider")
    if prov is not None and not isinstance(prov, dict):
        raise op.ProviderError(f"{path} 里的 provider 不是对象：没有改它")
    kept = isinstance(prov, dict) and pid in prov
    if not kept:
        cfg["provider"] = {**(prov or {}), pid: json.loads(json.dumps(p["block"]))}
        if not dry_run:
            op._write(path, cfg)
    return {"file": path, "plan": name, "provider": pid, "kept": kept, "written": not kept and not dry_run,
            "model": f"{pid}/{model}", "vision": model in VISION, "key_env": p["key_env"],
            "models": [f"{pid}/{m}" for m in models], "next": steps(name, model)}


def steps(name: str, model: str | None = None) -> list[str]:
    from . import service
    p = plan(name)
    env_file = service.env_file(service.name())
    card = (f"agentj secret request --name {p['key_env']} --purpose '阿里百炼 {LABEL[name]} 的专属 key（OpenCode 用）' "
            f"--dest env:{env_file}#{p['key_env']}")
    if p.get("verify_url"):
        card += f" --verify-url {p['verify_url']}"
    return [card, f"agentj agent switch opencode --model {p['provider']}/{model or p['model']}"]


def facts(environ=None) -> list[dict]:
    """For doctor / detect: each Bailian provider in OpenCode's config — id, plan, the key's NAME and whether the service
    environment file has it (never the value)."""
    from . import provider_runtime
    out = []
    try:
        listed = op.listing(environ)
    except op.ProviderError:
        return out
    try:
        fresh = provider_runtime.fresh_environment({})
    except Exception:  # noqa: BLE001 — no service: nothing known
        fresh = {}
    e = os.environ if environ is None else environ
    for row in listed:
        p = by_provider(row["provider"])
        if p is None:
            continue
        k = row.get("key_env")
        out.append({"provider": row["provider"], "plan": p["plan"], "key_env": k, "inline_key": row["inline_key"],
                    "key_set": bool(k and (fresh.get(k) or e.get(k)))})
    return out


def model_note(model: str | None, lang: str = "zh") -> str | None:
    """A one-line note when the selected OpenCode model is a Bailian plan's (terms + whether it reads images)."""
    pid = (model or "").split("/", 1)[0]
    p = by_provider(pid)
    if p is None:
        return None
    m = (model or "").split("/", 1)[1] if "/" in (model or "") else ""
    eye = ("" if m in VISION else
           f"（{m} 不能看图：截图、浏览器自动化要看图时换 {p['provider']}/{p['model']} 或 {p['model_light']}）" if lang == "zh" else
           f" ({m} cannot read images: for screenshots / browser work use {p['provider']}/{p['model']} or {p['model_light']})")
    return terms(p["plan"], lang) + eye


def render(r: dict, lang: str = "zh") -> str:
    if lang == "zh":
        head = (f"{r['file']} 里已经有 {r['provider']}：保持你原来的配置，没有改。" if r["kept"] else
                ("已写入" if r["written"] else "（预演，未写入）") + f" {r['file']}：服务商 {r['provider']}（{len(r['models'])} 个模型），"
                f"key 从环境变量 {r['key_env']} 读（这里不存 key）。")
        lines = [head, f"价格：{tiers_text(r['plan'], 'zh')}", "接下来：",
                 f"  1. {r['next'][0]}\n     （手机上弹出密钥卡，主人只把百炼控制台「套餐专属 API Key」贴进卡片；Agent 看不到值）",
                 f"  2. {r['next'][1]}" + ("（这个模型能看图：截图、浏览器自动化都能用）" if r["vision"] else ""), "⚠ " + terms(r.get("plan"), "zh")]
    else:
        head = (f"{r['provider']} already exists in {r['file']}: your own configuration is kept unchanged." if r["kept"] else
                ("Written" if r["written"] else "(dry run, nothing written)") + f" {r['file']}: provider {r['provider']} "
                f"({len(r['models'])} models), key read from {r['key_env']} (no key stored here).")
        lines = [head, f"Prices: {tiers_text(r['plan'], 'en')}", "Next:",
                 f"  1. {r['next'][0]}\n     (a key card opens on the phone; the owner pastes the plan's own API key there only)",
                 f"  2. {r['next'][1]}" + (" (this model reads images: screenshots, browser work)" if r["vision"] else ""), "⚠ " + terms(r.get("plan"), "en")]
    if r.get("plan") == "token-plan":   # P311: the media kit runs on the same Token Plan key
        lines.append("装好后可以说「装千问自媒体工具」：出图、出片、配音。" if lang == "zh" else
                     "Once set up, say 「装千问自媒体工具」 (install the Qwen media kit) for images, video and voice-over.")
    return "\n".join(lines)


def cmd(a) -> int:
    lang = "en" if getattr(a, "lang", None) == "en" else "zh"
    try:
        r = install(a.plan or "token-plan", (a.model or [None])[0], a.dry_run)
    except op.ProviderError as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False) if a.json else f"没有改：{e}",
              file=sys.stdout if a.json else sys.stderr)
        return 2
    print(json.dumps({"ok": True, **r, "terms": TERMS}, ensure_ascii=False) if a.json else render(r, lang))
    return 0
