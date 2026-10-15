"""P315 (Leo, 2026-10-15): Volcano Engine Ark (火山方舟) Coding Plan — `agentj provider ark [--model …] [--harness claude]`.

The third mainland China plan next to Kimi and Bailian (ai_route.py): one subscription (Lite / Pro; Kimi K3 only in Pro)
with ByteDance Doubao, Moonshot Kimi, Zhipu GLM, DeepSeek and MiniMax models. `ark.json` is the one place for its models:
which read images (measured by the owner, support thread st_ASwQAUOspTnh8jMvSeueKA: kimi-k3, doubao-seed-2.1-pro …
yes; glm-5.3, deepseek-v4-pro text only) and output limits (GLM-5.3's thinking used up OpenCode's 32k default).

- OpenCode: the OpenAI-compatible block (…/api/coding/v3) with the key as {env:ARK_CODING_PLAN_API_KEY}; an existing
  provider of the same id stays exactly as it is (`kept`), no top-level `model` is written.
- Claude Code: a provider profile on the Anthropic-compatible address (…/api/coding) — `provider_profiles` points the
  HAIKU / SONNET / OPUS defaults and the subagent model at the same Ark model, so background work never asks Ark for a
  Claude model name. This module only prints the three commands; the key goes to the profile's private key file.

The key only through the phone's key card (`agentj secret request`); this module never reads it. Ark's coding address has
no published read-only check, so the card stores without one (like Bailian's Coding Plan) and the first reply is the check.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys

from . import opencode_provider as op

DATA = pathlib.Path(__file__).with_name("ark.json")
PROFILE = "ark-claude"

TERMS = {
    "zh": "火山方舟 Coding Plan 是给编程工具交互使用的套餐（条款以方舟控制台为准）：定时任务、客服 bot 这类无人值守的活请用按量付费的方舟 "
          "API Key——Agent J 不会把 Coding Plan 的 Key 交给定时任务或 bot。Coding Plan 不含生图模型。",
    "en": "Volcano Ark Coding Plan is a plan for interactive use inside coding tools (Ark's console terms are authoritative): "
          "unattended work such as scheduled tasks or support bots needs a pay-as-you-go Ark key — Agent J never gives the "
          "Coding Plan key to scheduled tasks or bots. The Coding Plan has no image-generation models.",
}


def data() -> dict:
    return json.loads(DATA.read_text(encoding="utf-8"))


def vision(model: str) -> bool:
    m = data()["block"]["models"].get(model) or {}
    return "image" in (m.get("modalities") or {}).get("input", [])


def verify_for(pid: str | None) -> tuple[str, str] | None:
    """Same contract as bailian.verify_for: ("", "") = an Ark provider without a read-only check; None = not Ark."""
    d = data()
    if pid != d["provider"]:
        return None
    return (d["verify_url"], op.HEADER["openai"]) if d.get("verify_url") else ("", "")


def _model(model: str | None) -> str:
    d = data()
    model = model or d["model"]
    if model not in d["block"]["models"]:
        raise op.ProviderError(f"火山方舟 Coding Plan 没有模型 {model}（可选：{', '.join(sorted(d['block']['models']))}）")
    return model


def install(model: str | None = None, dry_run: bool = False, environ=None) -> dict:
    """Add the Coding Plan block to OpenCode's config unless a provider with the same id is there (then: kept untouched)."""
    d = data()
    pid, model = d["provider"], _model(model)
    path = op.config_file(environ)
    cfg = op._load(path)
    prov = cfg.get("provider")
    if prov is not None and not isinstance(prov, dict):
        raise op.ProviderError(f"{path} 里的 provider 不是对象：没有改它")
    kept = isinstance(prov, dict) and pid in prov
    if not kept:
        cfg["provider"] = {**(prov or {}), pid: json.loads(json.dumps(d["block"]))}
        if not dry_run:
            op._write(path, cfg)
    models = sorted(d["block"]["models"])
    return {"file": path, "harness": "opencode", "provider": pid, "kept": kept, "written": not kept and not dry_run,
            "model": f"{pid}/{model}", "vision": vision(model), "key_env": d["key_env"],
            "models": [f"{pid}/{m}" for m in models], "vision_models": [f"{pid}/{m}" for m in models if vision(m)],
            "pro_only": d["pro_only"], "next": steps(model)}


def steps(model: str | None = None) -> list[str]:
    from . import service
    d = data()
    env_file = service.env_file(service.name())
    card = (f"agentj secret request --name {d['key_env']} --purpose '火山方舟 Coding Plan 的 API Key（OpenCode 用）' "
            f"--dest env:{env_file}#{d['key_env']}")
    if d.get("verify_url"):
        card += f" --verify-url {d['verify_url']}"
    return [card, "agentj service restart", f"agentj agent switch opencode --model {d['provider']}/{_model(model)}"]


def claude_steps(model: str | None = None) -> list[str]:
    """Claude Code on Ark: a provider profile — every tier model and the subagent model point at the same Ark model."""
    model = _model(model)
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
    pid = data()["provider"]
    return [{"provider": r["provider"], "key_env": r.get("key_env"), "inline_key": r["inline_key"],
             "key_set": bool(r.get("key_env") and (fresh.get(r["key_env"]) or e.get(r["key_env"])))}
            for r in listed if r["provider"] == pid]


def model_note(model: str | None, lang: str = "zh") -> str | None:
    """A one-line note when the selected OpenCode model is the Coding Plan's (terms + whether it reads images)."""
    d = data()
    pid, _, m = (model or "").partition("/")
    if pid != d["provider"]:
        return None
    eye = ("" if vision(m) else
           f"（{m} 不能看图：截图、浏览器自动化要看图时换 {pid}/{d['model']} 或 {pid}/{d['model_strong']}）" if lang == "zh" else
           f" ({m} cannot read images: for screenshots / browser work use {pid}/{d['model']} or {pid}/{d['model_strong']})")
    pro = ("（kimi-k3 只在 Pro 套餐里）" if lang == "zh" else " (kimi-k3 is in the Pro plan only)") if m in d["pro_only"] else ""
    return TERMS[lang] + eye + pro


def render(r: dict, lang: str = "zh") -> str:
    d = data()
    if r["harness"] == "claude":
        if lang == "zh":
            return "\n".join(["Claude Code 接火山方舟（Anthropic 兼容地址 …/api/coding）：", *(f"  {i}. {s}" for i, s in enumerate(r["next"], 1)),
                              "  第 2 步手机弹出密钥卡，主人只把方舟控制台的 API Key 贴进卡片；第 3 步先真实发一句测试，通过才写配置。",
                              "  HAIKU / SONNET / OPUS 默认模型和子 agent 模型都会指向同一个方舟模型（另指：--haiku-model 等）。",
                              "⚠ " + TERMS["zh"]])
        return "\n".join(["Claude Code on Volcano Ark (Anthropic-compatible …/api/coding):", *(f"  {i}. {s}" for i, s in enumerate(r["next"], 1)),
                          "  Step 2 opens a key card on the phone; step 3 sends one real test message before writing anything.",
                          "  The HAIKU / SONNET / OPUS defaults and the subagent model all point at the same Ark model (others: --haiku-model …).",
                          "⚠ " + TERMS["en"]])
    eyes = ", ".join(m.split("/", 1)[1] for m in r["vision_models"])
    if lang == "zh":
        head = (f"{r['file']} 里已经有 {r['provider']}：保持你原来的配置，没有改。" if r["kept"] else
                ("已写入" if r["written"] else "（预演，未写入）") + f" {r['file']}：服务商 {r['provider']}（{len(r['models'])} 个模型），"
                f"key 从环境变量 {r['key_env']} 读（这里不存 key）。")
        return "\n".join([head, f"能看图的模型：{eyes}；glm-5.3、deepseek-v4-pro 等是纯文本。kimi-k3 只在 Pro 套餐里。", "接下来：",
                          f"  1. {r['next'][0]}\n     （手机上弹出密钥卡，主人只把方舟控制台 {d['console_url']} 的 API Key 贴进卡片；Agent 看不到值）",
                          f"  2. {r['next'][1]}", f"  3. {r['next'][2]}" + ("（这个模型能看图）" if r["vision"] else ""),
                          f"想用 Claude Code：`agentj provider ark --harness claude`。", "⚠ " + TERMS["zh"]])
    head = (f"{r['provider']} already exists in {r['file']}: your own configuration is kept unchanged." if r["kept"] else
            ("Written" if r["written"] else "(dry run, nothing written)") + f" {r['file']}: provider {r['provider']} "
            f"({len(r['models'])} models), key read from {r['key_env']} (no key stored here).")
    return "\n".join([head, f"Models that read images: {eyes}; glm-5.3, deepseek-v4-pro and others are text only. kimi-k3 is Pro only.",
                      "Next:", f"  1. {r['next'][0]}\n     (a key card opens on the phone; the owner pastes the Ark API key there only)",
                      f"  2. {r['next'][1]}", f"  3. {r['next'][2]}" + (" (this model reads images)" if r["vision"] else ""),
                      "For Claude Code: `agentj provider ark --harness claude`.", "⚠ " + TERMS["en"]])


def cmd(a) -> int:
    lang = "en" if getattr(a, "lang", None) == "en" else "zh"
    model = (a.model or [None])[0]
    try:
        if getattr(a, "harness", None) == "claude":
            r = {"harness": "claude", "model": _model(model), "next": claude_steps(model)}
        else:
            r = install(model, a.dry_run)
    except op.ProviderError as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False) if a.json else f"没有改：{e}",
              file=sys.stdout if a.json else sys.stderr)
        return 2
    print(json.dumps({"ok": True, **r, "terms": TERMS}, ensure_ascii=False) if a.json else render(r, lang))
    return 0
