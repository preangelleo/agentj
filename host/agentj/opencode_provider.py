"""`agentj provider add|list|remove` (P60, F25): a third-party model service — a base URL plus a key, OpenAI-compatible or
Anthropic-compatible — declared once in the owner's OpenCode configuration, so `--model <id>/<model>` works in OpenCode v1
and v2 alike (both measured with OpenRouter as a plain OpenAI-compatible base URL, 1.18.32 and 2.0.23).

What it writes: `provider.<id>` in `$XDG_CONFIG_HOME/opencode/opencode.json` (other keys untouched, the previous file kept as
`opencode.json.agentj-bak`) with `options.apiKey = "{env:<ID>_API_KEY}"` — a NAME. The key itself never passes through here
or through the Agent: the owner pastes it on the phone's secret card (`agentj secret request`, F17), which writes it into the
service's environment file; the service restart makes it visible to the OpenCode that Agent J starts.

P315 capabilities (OpenCode's own model keys): `--vision` writes `attachment: true` + `modalities.input: [text, image]`
(without them OpenCode never sends a picture), `--no-vision` the text-only pair; `--max-output N [--context N]` writes
`limit.output` / `limit.context` (OpenCode needs both; GLM-5.3's thinking alone can use up a 32k output budget). Adding
again MERGES: other models of the same provider and keys the owner added by hand stay.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from urllib.parse import urlparse

ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,119}$")
NPM = {"openai": "@ai-sdk/openai-compatible", "anthropic": "@ai-sdk/anthropic"}
HEADER = {"openai": "Authorization: Bearer {value}", "anthropic": "x-api-key: {value}"}
# OpenCode's own catalogue ids: a custom block with the same id would silently change the built-in provider
RESERVED = ("opencode", "openai", "anthropic", "openrouter", "deepseek", "google", "zhipuai", "moonshotai", "github-copilot")


class ProviderError(Exception):
    pass


def config_file(environ=None) -> str:
    e = os.environ if environ is None else environ
    base = e.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "opencode", "opencode.json")


def key_env(pid: str) -> str:
    return re.sub(r"[^A-Z0-9]", "_", pid.upper()) + "_API_KEY"


def _check_url(url: str) -> str:
    u = urlparse(url or "")
    local = u.hostname in ("127.0.0.1", "localhost", "::1")
    if u.scheme not in ("https", "http") or not u.hostname or (u.scheme == "http" and not local) or u.username or u.password:
        raise ProviderError("base_url 必须是 https://…（本机服务可用 http://127.0.0.1），且不能带账号密码 / base_url must be https "
                            "(http only for localhost) without credentials")
    if u.query or u.fragment:
        raise ProviderError("base_url 不能带 ? 或 # / no query or fragment in base_url")
    return url.rstrip("/")


DEFAULT_CONTEXT = 128000   # P315: only when --max-output comes without --context and nothing is configured yet


def capabilities(vision: bool | None = None, max_output: int | None = None, context: int | None = None) -> dict:
    """P315: the per-model keys OpenCode reads — image input and the output / context limits ({} = say nothing)."""
    out: dict = {}
    if vision is not None:
        out["attachment"] = bool(vision)
        out["modalities"] = {"input": ["text", "image"] if vision else ["text"], "output": ["text"]}
    for label, v in (("--max-output", max_output), ("--context", context)):
        if v is not None and (not isinstance(v, int) or isinstance(v, bool) or not 1024 <= v <= 10_000_000):
            raise ProviderError(f"{label} 要是 1024 到 10000000 之间的整数（token 数） / {label}: tokens, 1024–10000000")
    if max_output is not None or context is not None:
        out["limit"] = {k: v for k, v in (("context", context), ("output", max_output)) if v is not None}
    return out


def _merge_model(previous, new: dict) -> dict:
    """`new` over `previous`, `limit` merged key by key; an output limit without a context gets DEFAULT_CONTEXT."""
    prev = previous if isinstance(previous, dict) else {}
    out = {**prev, **new}
    lim = {**(prev.get("limit") if isinstance(prev.get("limit"), dict) else {}), **new.get("limit", {})}
    if lim:
        lim.setdefault("context", DEFAULT_CONTEXT)
        out["limit"] = lim
    return out


def block(pid: str, base_url: str, models: list[str], api: str = "openai", name: str | None = None,
          env: str | None = None, caps: dict | None = None) -> dict:
    if not ID_RE.match(pid or ""):
        raise ProviderError("服务商 id 只能用小写字母、数字、- 和 _（例如 myrelay） / provider id: lowercase letters, digits, - and _")
    if pid in RESERVED:
        raise ProviderError(f"「{pid}」是 OpenCode 自带的服务商：它的 key 用 `opencode auth login {pid}` 存，不需要这条命令 / "
                            f"{pid} is built into OpenCode: store its key with `opencode auth login {pid}`")
    if api not in NPM:
        raise ProviderError("--api 只能是 openai 或 anthropic")
    if not models or any(not MODEL_RE.match(m) for m in models):
        raise ProviderError("至少给一个模型 id（--model），只能是字母数字和 . _ : / @ + - / give at least one valid --model")
    env = env or key_env(pid)
    if not re.match(r"^[A-Z][A-Z0-9_]{1,63}$", env):
        raise ProviderError("--key-env 要像 MYRELAY_API_KEY 这样的变量名")
    return {"npm": NPM[api], "name": (name or pid)[:60],
            "options": {"baseURL": _check_url(base_url), "apiKey": "{env:" + env + "}"},
            "models": {m: _merge_model({"name": m}, caps or {}) for m in models}}


def _load(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read()
    except FileNotFoundError:
        return {"$schema": "https://opencode.ai/config.json"}
    try:
        cfg = json.loads(raw) if raw.strip() else {}
    except ValueError:
        raise ProviderError(f"{path} 不是合法 JSON（可能带注释）：没有改它。请先修好或删掉注释 / not plain JSON, left untouched") from None
    if not isinstance(cfg, dict):
        raise ProviderError(f"{path} 不是 JSON 对象：没有改它")
    return cfg


def _write(path: str, cfg: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        with open(path, "rb") as src, open(path + ".agentj-bak", "wb") as dst:
            dst.write(src.read())
    tmp = path + ".agentj-tmp"
    mode = os.stat(path).st_mode & 0o777 if os.path.exists(path) else 0o600
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def add(pid: str, base_url: str, models: list[str], api: str = "openai", name: str | None = None, env: str | None = None,
        dry_run: bool = False, environ=None, vision: bool | None = None, max_output: int | None = None,
        context: int | None = None) -> dict:
    path = config_file(environ)
    b = block(pid, base_url, models, api, name, env, capabilities(vision, max_output, context))
    cfg = _load(path)
    prov = cfg.get("provider")
    if prov is not None and not isinstance(prov, dict):
        raise ProviderError(f"{path} 里的 provider 不是对象：没有改它")
    old = (prov or {}).get(pid)
    if isinstance(old, dict):   # P315: merge — the owner's other models and hand-added keys stay
        om = old.get("models") if isinstance(old.get("models"), dict) else {}
        oo = old.get("options") if isinstance(old.get("options"), dict) else {}
        caps = capabilities(vision, max_output, context)
        b = {**old, **b, "options": {**oo, **b["options"]},
             "models": {**om, **{m: _merge_model({"name": m, **(om[m] if isinstance(om.get(m), dict) else {})}, caps)
                                for m in models}}}
    cfg["provider"] = {**(prov or {}), pid: b}
    if not dry_run:
        _write(path, cfg)
    var = b["options"]["apiKey"][5:-1]
    return {"file": path, "provider": pid, "models": [f"{pid}/{m}" for m in models], "key_env": var,
            "written": not dry_run, "vision": [f"{pid}/{m}" for m in models if b["models"][m].get("attachment") is True], "next": next_steps(pid, var, b["options"]["baseURL"], api, models[0])}


def next_steps(pid: str, var: str, base_url: str, api: str, model: str) -> list[str]:
    from . import service
    env_file = service.env_file(service.name())
    verify = f"{base_url}/models"
    return [
        f"agentj secret request --name {var} --purpose '{pid} 模型服务的 key（OpenCode 用）' --dest env:{env_file}#{var} "
        f"--verify-url {verify} --verify-header '{HEADER[api]}'",
        f"agentj agent opencode --dir <工作目录> --model {pid}/{model}",
        "agentj service restart",
    ]


def remove(pid: str, environ=None) -> dict:
    path = config_file(environ)
    cfg = _load(path)
    prov = cfg.get("provider") if isinstance(cfg.get("provider"), dict) else {}
    if pid not in prov:
        return {"file": path, "provider": pid, "removed": False}
    prov = {k: v for k, v in prov.items() if k != pid}
    cfg["provider"] = prov
    _write(path, cfg)
    return {"file": path, "provider": pid, "removed": True}


def listing(environ=None) -> list[dict]:
    """Names only: id, base URL, the key's variable name and whether it is set in THIS environment, models."""
    cfg = _load(config_file(environ))
    e = os.environ if environ is None else environ
    out = []
    for pid, b in (cfg.get("provider") or {}).items():
        if not isinstance(b, dict):
            continue
        opts = b.get("options") if isinstance(b.get("options"), dict) else {}
        ref = opts.get("apiKey") if isinstance(opts.get("apiKey"), str) else ""
        m = re.fullmatch(r"\{env:([A-Za-z_][A-Za-z0-9_]*)\}", ref)
        out.append({"provider": pid, "base_url": opts.get("baseURL"), "key_env": m.group(1) if m else None,
                    "key_set_here": bool(m and e.get(m.group(1))), "inline_key": bool(ref and not m),
                    "models": sorted(b.get("models") or {})})
    return out


def cmd(a) -> int:
    if a.action == "ark":                     # P315: the Volcano Ark Coding Plan preset (ark.py)
        from . import ark
        return ark.cmd(a)
    if a.action == "bailian":                 # P310: the Bailian plan preset (bailian.py)
        from . import bailian
        if a.id and not a.plan:
            a.plan = a.id
        return bailian.cmd(a)
    try:
        if a.action == "add":
            if not a.id or not a.base_url:
                raise ProviderError("用法：agentj provider add <id> --base-url https://… --model <模型> [--api openai|anthropic]")
            r = add(a.id, a.base_url, a.model or [], a.api, a.name, a.key_env, a.dry_run,
                    vision=a.vision, max_output=a.max_output, context=a.context)
        elif a.action == "remove":
            if not a.id:
                raise ProviderError("用法：agentj provider remove <id>")
            r = remove(a.id)
        else:
            r = {"providers": listing()}
    except ProviderError as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False) if a.json else f"没有改：{e}", file=sys.stderr if not a.json else sys.stdout)
        return 2
    if a.json:
        print(json.dumps({"ok": True, **r}, ensure_ascii=False))
        return 0
    if a.action == "add":
        print(("已写入" if r["written"] else "（预演，未写入）") + f" {r['file']}：服务商 {r['provider']}，模型 {', '.join(r['models'])}；"
              f"key 从环境变量 {r['key_env']} 读（这里不存 key）。" +
              (f"能看图：{', '.join(r['vision'])}。" if r["vision"] else ""))
        print("接下来：\n  1. " + r["next"][0] + "\n     （手机上弹出密钥卡，主人贴 key；Agent 看不到值）\n  2. " + r["next"][1] +
              "\n  3. " + r["next"][2])
    elif a.action == "remove":
        print(("已删除" if r["removed"] else "没有这个服务商：") + f" {r['provider']}（{r['file']}）")
    else:
        for p in r["providers"]:
            print(f"{p['provider']}  {p['base_url']}  key={p['key_env'] or ('写在文件里' if p['inline_key'] else '—')}"
                  f"{'（本环境已设置）' if p['key_set_here'] else ''}  模型：{', '.join(p['models'])}")
        if not r["providers"]:
            print("OpenCode 配置里还没有自定义服务商。")
    return 0


def add_parser(sub) -> None:
    sp = sub.add_parser("provider", help="OpenCode 的第三方模型服务（base_url + key）：add / list / remove；ark = 火山方舟 Coding Plan "
                                         "预设（中国大陆首推，先买 Lite）；bailian = 阿里百炼 Token Plan 预设 · a third-party model "
                                         "service for OpenCode (base URL + key); ark = the Volcano Ark Coding Plan preset (first "
                                         "choice in mainland China, Lite first); bailian = the Alibaba Cloud Bailian Token Plan preset",
                        description="Declares an OpenAI- or Anthropic-compatible service in OpenCode's config; the key is "
                                    "only referenced by name ({env:<ID>_API_KEY}) and is pasted on the phone's secret card.")
    sp.add_argument("action", choices=["add", "list", "remove", "bailian", "ark"])
    sp.add_argument("id", nargs="?", help="服务商 id，例如 myrelay（模型写成 myrelay/<模型>）")
    sp.add_argument("--base-url", dest="base_url", help="例如 https://api.example.com/v1")
    sp.add_argument("--model", action="append", help="模型 id，可重复")
    sp.add_argument("--api", choices=sorted(NPM), default="openai", help="openai（默认，OpenAI 兼容）或 anthropic（Anthropic 兼容）")
    sp.add_argument("--name", help="显示名")
    sp.add_argument("--key-env", dest="key_env", help="key 的环境变量名（默认 <ID>_API_KEY）")
    sp.add_argument("--vision", action=argparse.BooleanOptionalAction, default=None,
                    help="add：这些模型能看图（--no-vision = 纯文本）/ the models read images (--no-vision: text only)")
    sp.add_argument("--max-output", dest="max_output", type=int, help="add：输出上限 token / output limit in tokens")
    sp.add_argument("--context", type=int, help="add：上下文长度 token（配 --max-output，默认 128000）/ context window")
    sp.add_argument("--plan", choices=["token-plan", "coding-plan"],
                    help="bailian：token-plan（默认，个人版，含 Qwen 3.8 Max）或 coding-plan / which Bailian plan")
    sp.add_argument("--harness", choices=["opencode", "claude"],
                    help="ark：opencode（默认）或 claude（打印 Claude Code 的 profile 三步）/ ark: which harness")
    sp.add_argument("--lang", choices=["zh", "en"], help="bailian / ark：说明文字的语言 / text language")
    sp.add_argument("--dry-run", dest="dry_run", action="store_true")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=lambda a: sys.exit(cmd(a)))
