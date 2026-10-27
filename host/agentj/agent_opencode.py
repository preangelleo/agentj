"""OpenCode adapter (PROTOCOL §8, ADR-A55 – A58): the phone talks to the customer's own OpenCode through its official
headless server, `opencode serve`, with every permission request answered on the phone.

Why `serve` and not `opencode run`: `run` answers its own permission prompts (reject, or once with `--auto`) — nothing outside
can take part. `serve` publishes `permission.asked` on its event stream (`GET /event`, SSE) and waits for
`POST /permission/{id}/reply {"reply": "once" | "reject"}`.

What serve does (measured on OpenCode 1.18.32, `reports/qa/opencode/`):
- starts `opencode serve --hostname 127.0.0.1 --port <random>` inside the fence (fence.py, like Claude Code), with a fresh
  random `OPENCODE_SERVER_PASSWORD` per start (HTTP Basic, user `opencode`) that lives only in serve's memory and the
  process's environment; models.dev fetch, LSP downloads, auto-update, share and the embedded web UI are switched off;
- keeps one conversation (session id in `agent.json`, like the other adapters) and sends each phone message with
  `POST /session/{id}/prompt_async`; the turn ends with `session.idle`; finished text parts (`message.part.updated` with
  `time.end`) go to the phone as they complete; the event stream is re-opened when it drops (and missed text / requests are
  fetched again);
- **our permission rules ride on the session, not on the config** (ADR-A56): OpenCode evaluates the LAST matching rule of
  `agent rules (defaults + every config file) + session rules`, while config files are deep-merged (a `deny` the human wrote
  could be overwritten by an `ask` of ours, and a `"*": "allow"` they wrote lands after ours). The session ruleset = ours
  (everything asks, except read-only tools and a short read-only bash list; the question tool is allowed; a sub-agent only for
  the agents opencode_subagents verified carry the same rules — P426) followed by
  every `deny` rule of the human's own agent ruleset again — so a human `allow` becomes a question to the phone, a human `deny`
  stays a deny, and no config file (also one the Agent writes) can loosen it;
- answers `once` or `reject` (with the reason, so the model is told), never `always` (that would add an allow rule inside
  OpenCode for the rest of the process). A reply that serve did not send (`permission.replied` once / always) means something
  else used the server's password: OpenCode is stopped at once (G-A66).

The stop switch and `/stop` call `POST /session/{id}/abort` before the process tree ends (ADR-A72); slash commands use
`POST /session/{id}/summarize`, `GET /config/providers`, `GET /session/{id}` and the messages' token counts (slash.py). A
scheduled task (`agentj tasks`) runs in its own throw-away session; `mode: research` turns every "ask" of our rules into "deny".
"""
from __future__ import annotations

from . import resource_policy

import asyncio
import base64
import contextlib
import json
import os
import re
import secrets
import signal
import socket
import tempfile
import time

from . import bg, humane, provenance, slash
from .agent import LINE_LIMIT, UNCERTAIN, Agent, _bin, _signal_tree
from .slash import Result
from .envcompat import getenv
from .text import clean, clean_line, strip_reasoning

START_WAIT = 60          # seconds with no startup output before failing
START_MAX = 300          # bounded cold start even if output continues
TURN_IDLE = 300          # no meaningful turn progress; SSE heartbeats do not count
CONNECT_WAIT = 15        # s for the event stream to connect before the first message is sent
SSE_IDLE = 90            # s without any byte (OpenCode sends heartbeats) before the stream is re-opened
REQ_TIMEOUT = 30
WATCH = float(getenv("AGENTJ_TEST_OC_WATCH", "15"))   # s between "is the turn still running?" checks
MAX_BODY = 32 * 1024 * 1024
USER = "opencode"

# ---------------------------------------------------------------- permission rules (ADR-A56)
# Read-only bash commands that run without a card (OpenCode matches each sub-command of a pipeline / list separately; a
# pattern ending in " *" also matches the bare command). Every one is low risk for danger.classify (test_opencode checks).
BASH_READ_ONLY = ("ls *", "pwd", "cat *", "git status *", "git diff *", "git log *")
# …but back to "ask" when the sub-command redirects, substitutes, chains or names an output file (OpenCode keeps
# redirections and substitutions inside the sub-command's text): `cat a > ~/.bashrc`, `ls $(rm x)`, `git diff --output=f`.
BASH_BACK_TO_ASK = ("*>*", "*<*", "*$*", "*`*", "*|*", "*;*", "*&*", "*\n*", "*--output*", "*--ext-diff*", "*--textconv*")
READ_ONLY_TOOLS = ("glob", "grep", "list", "lsp", "todowrite", "todoread", "skill", "webfetch")


def _rule(permission: str, pattern: str, action: str) -> dict:
    return {"permission": permission, "pattern": pattern, "action": action}


def our_rules(subagents=()) -> list[dict]:
    """The rules serve adds to the session, in order (last match wins). subagents (P426): agents whose own rules were seen
    to carry exactly ours (opencode_subagents) — `task <name> allow` after the deny; every other agent stays denied."""
    r = [_rule("*", "*", "ask"),
         _rule("read", "*", "allow"), _rule("read", "*.env", "ask"), _rule("read", "*.env.*", "ask"),
         _rule("read", "*.env.example", "allow")]
    r += [_rule(t, "*", "allow") for t in READ_ONLY_TOOLS]
    r += [_rule("question", "*", "allow"),  # §10.7: the model may ASK the human — question.asked → a card on the phone
          _rule("task", "*", "deny")]       # subagent sessions inherit only deny rules, not ours (G-A65)
    r += [_rule("task", n, "allow") for n in subagents]
    r += [_rule("bash", p, "allow") for p in BASH_READ_ONLY]
    r += [_rule("bash", p, "ask") for p in BASH_BACK_TO_ASK]
    return r


def _own_dir_rule(rule: dict) -> bool:
    """OpenCode's own scratch folders (truncated tool output, its temp dir): the human's agent rules allow them; keep that."""
    p = rule.get("pattern") or ""
    return (rule.get("permission") == "external_directory" and rule.get("action") == "allow"
            and (p.endswith("/opencode/tool-output/*") or p in {os.path.join(d, "opencode", "*")
                                                                 for d in ("/tmp", tempfile.gettempdir())}))


def session_rules(agent_rules, research: bool = False, subagents=()) -> list[dict]:
    """Ours, then OpenCode's own scratch-folder allows, then every deny of the human's agent ruleset (so it stays deny).
    research (a scheduled `mode: research` run): every "ask" of ours is a "deny" — only the read-only allows remain."""
    agent_rules = [r for r in (agent_rules or []) if isinstance(r, dict) and all(isinstance(r.get(k), str)
                                                                                for k in ("permission", "pattern", "action"))]
    own = [_rule(r["permission"], r["pattern"], r["action"]) for r in agent_rules if _own_dir_rule(r)]
    # every deny of the human's ruleset — except OpenCode's own built-in `question` deny that its defaults allow again right
    # after (PROMPT-33 §10.7: the model may ask the human); a human whose ruleset really ends in a question deny keeps it
    q_off = evaluate("question", "*", agent_rules) == "deny"
    deny = [_rule(r["permission"], r["pattern"], "deny") for r in agent_rules if r["action"] == "deny"
            and (r["permission"] != "question" or q_off)]
    ours = our_rules(() if research else subagents)
    if research:          # a read-only scheduled run asks nobody anything: every ask (and the question tool) is a deny
        ours = [dict(r, action="deny") if r["action"] == "ask" or r["permission"] == "question" else r for r in ours]
    out = ours + own + deny
    seen, dedup = set(), []
    for r in out:                       # keep the LAST copy of a duplicate (only the last one can decide)
        k = (r["permission"], r["pattern"], r["action"])
        if k in seen:
            dedup = [x for x in dedup if (x["permission"], x["pattern"], x["action"]) != k]
        seen.add(k)
        dedup.append(r)
    return dedup


def wildcard(s: str, pattern: str) -> bool:
    """OpenCode's Wildcard.match (1.18): `*` = anything, `?` = one char, a trailing " *" also matches nothing; whole string."""
    s = s.replace("\\", "/")
    a = re.sub(r"[.+^${}()|\[\]\\]", lambda m: "\\" + m.group(0), pattern.replace("\\", "/"))
    a = a.replace("*", ".*").replace("?", ".")
    if a.endswith(" .*"):
        a = a[:-3] + "( .*)?"
    return re.fullmatch(a, s, re.S) is not None


def evaluate(permission: str, pattern: str, *rulesets) -> str:
    """OpenCode's Permission.evaluate: the last rule matching both permission and pattern; none → ask."""
    hit = "ask"
    for rs in rulesets:
        for r in rs:
            if wildcard(permission, r["permission"]) and wildcard(pattern, r["pattern"]):
                hit = r["action"]
    return hit


def config_content(user: str | None) -> str:
    """OPENCODE_CONFIG_CONTENT: the human's own (if any) with auto-update and share off. No permission here (ADR-A56)."""
    try:
        cfg = json.loads(user) if user else {}
    except ValueError:
        cfg = {}
    if not isinstance(cfg, dict):
        cfg = {}
    cfg.update(autoupdate=False, share="disabled")
    return json.dumps(cfg, separators=(",", ":"))


def turn_body(text: str, cfg: dict, main: bool) -> dict:
    """The prompt body of one phone turn (v1 API, owned and shared): `main` = Agent J's full constitution as `system`, else
    only the language line (an attached owner keeps their own identity). P142/P159: a hidden (synthetic) last part carries
    the per-turn reminder — the main Agent's address, delegate triage and language (main_identity.IDENTITY_REMINDER), or
    only the language for an attached owner — so OpenCode's own long agent prompt cannot outweigh them."""
    from . import main_identity
    lang = main_identity.language_of(cfg)
    return {"parts": [{"type": "text", "text": text},
                      {"type": "text", "text": main_identity.turn_reminder(cfg, main), "synthetic": True}],
            "system": main_identity.prompt(cfg) if main else main_identity.LANGUAGE_LINE[lang]}


_REGION = ("region", "country", "territory", "location is not supported", "not available in your", "unsupported_country")
_QUOTA_WINDOW = ("quota", "额度")
_QUOTA = ("quota", "insufficient", "balance", "billing", "credit", "payment")
_NETWORK = ("fetch failed", "certificate", "enotfound", "econnrefused", "econnreset", "etimedout", "connect timeout",
            "unable to connect", "socket hang up", "proxy", "tunnel")


def provider_error_reason(name, data, connected=None, provider=None, set_up=None):
    """session.error → one class (research 2026-10-04 §5: APIError.statusCode, ProviderAuthError, ProviderModelNotFoundError,
    UnknownError). The provider's own message / body is read only to classify, never logged or shown.
    P60 (measured, 1.18.32): a provider without a key in serve's view is no auth error but `UnknownError` "Model not found:
    <provider>/<model>" (the provider is missing from serve's provider state) — a model-not-found whose provider was not in
    serve's `connected` list at start (`connected`, `provider`) is `no_key`, never 「OpenCode 内部错误」.
    P423 #146: OpenCode's message cannot tell "the owner's key is invisible" from "a provider nobody ever set up": both are
    "Model not found: <p>/<m>. Did you mean: …" (1.18.34 getModel: a provider missing from serve's state suggests models of
    its CATALOGUE entry — deepinfra/google/gemma-4-31B-it suggested google/gemma-4-31B-it). Neither "Did you mean" nor a
    3-part model string separates them (P60's real case was openrouter/deepseek/…), and being in OpenCode's catalogue
    (GET /provider `all`, ~230 models.dev providers) proves nothing either. What does: whether the owner set the provider up
    anywhere we can see by NAME — `set_up` = auth.json entries + provider blocks of the configs (adapter: `set_up_providers`).
    Given that list, a provider in none of it is `model_missing` (wrong / ghost model: never a key card, never auth_rescue);
    one that is set up but absent from serve's view stays P60's `no_key`. `set_up` None (OpenCode v2, whose credential
    database is never read; a caller that cannot look) keeps P60's rule."""
    code = data.get('statusCode')
    code = code if type(code) is int and 100 <= code <= 599 else None
    body = str(data.get('responseBody') or '')[:2000]
    try:  # Providers can JSON-escape Chinese quota detail; decode only this bounded classification input.
        body = json.dumps(json.loads(body), ensure_ascii=False)
    except (ValueError, TypeError, RecursionError):
        pass
    detail = (str(data.get('message') or '') + ' ' + body).lower()
    lname = name.lower()
    if "freetiererror" in detail:      # P298: OpenCode Zen's free tier said no (warm-up, quota, a withdrawn model) — not access
        return "free_tier"
    if code == 451 or (code in (400, 403, None) and any(x in detail for x in _REGION)):
        return "region"
    from .auth_rescue import USAGE_LIMIT_RE     # P423 #138: the one used-up-window test every harness shares
    if (code == 429 and (any(x in detail for x in _QUOTA_WINDOW) or USAGE_LIMIT_RE.search(detail)
                         or ("exceeded your" in detail and not re.search(r"rate[ _-]*limit", detail)))) \
            or (code in (401, 403, None) and USAGE_LIMIT_RE.search(detail)):   # Kimi: 403 "monthly usage limit", an auth error
        return "quota_window"
    if code == 402 or (code in (403, 429) and any(x in detail for x in _QUOTA)):
        return "balance"
    reason = ("login" if code == 401 or "auth" in lname or "key" in lname
              else "access" if code == 403 else "rate_limit" if code == 429
              else "model" if code == 404 or "modelnotfound" in lname or "model not found" in detail or "modelnotfound" in detail
              else "network" if any(x in detail for x in _NETWORK)
              else "internal" if name == "UnknownError" else "provider")
    if reason == "model" and provider and isinstance(connected, list) and provider not in connected:
        return "model_missing" if isinstance(set_up, (list, set, frozenset, tuple)) and provider not in set_up else "no_key"
    return reason


def set_up_providers(extra=()) -> set:
    """P423 #146: provider ids the owner set up anywhere this host can see — names only, never a value: the keys of OpenCode
    v1's auth.json (each entry's content is dropped unread), `provider` blocks of the global opencode.json (Agent J's
    `agentj provider add` file) and of OPENCODE_CONFIG_CONTENT, plus `extra` (serve's own /config providers — project
    configs included — and `connected`)."""
    from .provider_runtime import configured
    out = {x for x in extra if isinstance(x, str)}
    out |= set(configured())
    data = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    try:
        with open(os.path.join(data, "opencode", "auth.json"), encoding="utf-8") as f:
            auth = json.load(f)
        out |= {k for k in auth if isinstance(k, str)} if isinstance(auth, dict) else set()
        del auth
    except (OSError, ValueError, UnicodeDecodeError):
        pass
    try:
        own = json.loads(os.environ.get("OPENCODE_CONFIG_CONTENT") or "{}")
        prov = own.get("provider") if isinstance(own, dict) else None
        out |= {k for k in prov if isinstance(k, str)} if isinstance(prov, dict) else set()
    except ValueError:
        pass
    return out


# One line per class, on the phone (fail_notice) and in `agentj doctor` (last turn's class). Provider.getLanguage caches the
# model client (with its key) per provider/model inside serve, without an invalidation hook — so a changed key needs a
# restart of `opencode serve`: Agent J does that itself before the next message (auth_fingerprint, P59 / A167).
PROVIDER_NOTES = {
    "login": "模型服务登录失败或 key 无效：在Agent J 主机运行 `opencode auth login` 换好 key，再发一条消息即可——Agent J 发现 key 变了会自动重启 OpenCode，接着这段对话。 / Provider login/key failed: run `opencode auth login`; Agent J restarts its OpenCode on the next message and keeps the conversation.",
    "region": "服务商不支持你所在的地区：让Agent J 主机上的 Agent 配好代理（例如说「帮我把代理设成 http://127.0.0.1:7890」），或改用 Claude Code。 / Provider unavailable in your region: set a proxy (agentj config set proxy.https …) or use Claude Code.",
    "access": "服务商拒绝访问（403）：核实地区、代理、账户权限及模型授权；403 不一定是 key 无效。 / Provider access denied: check region, proxy and account/model permission; 403 does not prove an invalid key.",
    "balance": "模型服务余额或额度不足：在服务商后台充值或查看额度，然后再试。 / Provider balance or quota exhausted; check your provider account.",
    "quota_window": "本周/本期额度已用完：到重置时间再试，或在服务商处升级套餐。 / Weekly/current-period quota exhausted: wait until the reset time or upgrade your plan with the provider.",
    "rate_limit": "模型服务限流：等一会儿再发；这条消息不会自动重发。 / Provider rate limit; wait and retry manually.",
    "no_key": "OpenCode 里没有「{provider}」可用的 key：只在终端里 export 的 key，后台运行的 Agent J 看不到。在Agent J 主机运行 `opencode auth login` 选 {provider} 存好 key，再发一条消息即可——Agent J 会自动重启 OpenCode 读到它。 / OpenCode has no key for {provider} (a key exported only in your shell is invisible to the Agent J service): store it with `opencode auth login`, then just send again.",
    "model_missing": "所选模型在 OpenCode 里用不了：服务商「{provider}」没有配置（或这个模型不存在）——不是 Key 的问题。在手机「模型和 API Key」换一个能用的模型；真要用 {provider}，先在Agent J 主机运行 `opencode auth login` 选 {provider} 存好 key。 / The selected model is not available in OpenCode: its provider {provider} is not set up (or the model does not exist) — not a key problem. Pick a working model in Models & API keys on the phone; to use {provider}, first store its key with `opencode auth login` on the Agent J host.",
    "model": "模型名称不可用：运行 `opencode models` 核实服务商/模型 ID，再用 `agentj agent opencode --model <provider/model>` 更新。 / Run `opencode models`, then update the model ID.",
    "network": "连不上模型服务：核实网络与代理设置（`agentj config get proxy.https`）。 / Provider connection failed; check network and proxy.",
    "internal": "OpenCode 内部错误：在Agent J 主机运行 `agentj doctor`，再让 Agent 运行 `agentj agent restart`。 / OpenCode internal error; run `agentj doctor`, then `agentj agent restart`.",
    "free_tier": "OpenCode 的免费通道暂时不让用（刚装好的头一两分钟常见，也可能是免费额度用完或这个免费模型下线了）：过一两分钟再发；还不行就在「模型和 API Key」换一个免费模型（opencode/…-free），或用你自己的模型。 / OpenCode's free tier refused for now (common right after installing; or the free quota is used up / the model was withdrawn): send again in a minute or two, or pick another free model or one of your own in Models & API keys.",
    "provider": "模型服务或 OpenCode 报错：在Agent J 主机运行 `agentj doctor`，核实模型名称、余额和网络。 / Provider/OpenCode error; run `agentj doctor`, then check model, balance and network.",
}


PROVIDER_LOGIN_ATTACHED = ("模型服务登录失败或 key 无效：在Agent J 主机运行 `opencode auth login` 换好 key，然后重启你Agent J 主机上原来的 OpenCode（它不是 Agent J 启动的）。"
                           " / Provider login/key failed: run `opencode auth login`, then restart your own OpenCode server.")


def auth_fingerprint() -> tuple:
    """Metadata of OpenCode v1's credential store and global config (inode, mtime, size) — never their content. A change
    means `opencode auth login` (or a config edit) happened since this serve started."""
    from .provider_runtime import env_path
    data = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    conf = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    out = []
    for p in (os.path.join(data, "opencode", "auth.json"), os.path.join(conf, "opencode", "opencode.json"),
              os.path.join(conf, "opencode", "opencode.jsonc"), env_path()):
        try:
            st = os.stat(p)
            out.append((st.st_ino, st.st_mtime_ns, st.st_size))
        except OSError:
            out.append(None)
    return tuple(out)


# P60: serve keeps its provider state (which providers have a key) and every model client (key baked in) for the life of
# the process (measured: a key fixed in auth.json still failed 401 / "Model not found" until serve restarted). The
# fingerprint above catches auth.json / config edits; a key that lives elsewhere (the service's environment file, OpenCode
# v2's credential database) is caught by this: after one of these failures an owned serve ends once the turn is over, so
# the next message starts a fresh one that reads the current keys.
RESTART_AFTER = ("login", "no_key", "model", "access", "region", "model_missing")


def opencode_env(base: dict, password: str) -> dict:
    env = dict(base)
    env.update(OPENCODE_SERVER_PASSWORD=password, OPENCODE_SERVER_USERNAME=USER, OPENCODE_DISABLE_MODELS_FETCH="1",
               OPENCODE_DISABLE_LSP_DOWNLOAD="1", OPENCODE_DISABLE_AUTOUPDATE="1", OPENCODE_DISABLE_SHARE="1",
               OPENCODE_DISABLE_EMBEDDED_WEB_UI="1", OPENCODE_CONFIG_CONTENT=config_content(base.get("OPENCODE_CONFIG_CONTENT")))
    return env


def startup_help(error: str, *, slow=False, lang='zh') -> str:
    """Classify locally, never put native output (possibly a key) on the phone."""
    text = error.lower()
    english = str(lang).startswith('en')
    if 'models.dev' in text:
        cause = ('Startup output suggests a model catalog fetch stalled; local catalog mode is enabled.' if english else
                 '根据启动输出，可能是模型目录下载卡住；已启用本地目录模式。')
    elif any(x in text for x in ('scanning', 'indexing', 'watcher')):
        cause = ('A large folder may be scanning. Choose a project folder instead of your home.' if english else
                 '可能正在扫描过大的工作目录。请改为专用项目文件夹，勿用用户主目录。')
    elif any(x in text for x in ('timeout', 'fetch failed', 'enotfound', 'econn')):
        cause = ('Startup output suggests a network problem; check your network and proxy.' if english else
                 '根据启动输出，可能是网络连接问题，请检查网络与代理。')
    else:
        cause = ('Cause is not known yet; check the network and use a project folder.' if english else
                 '尚未确定原因；请检查网络并使用专用项目文件夹。')
    if english:
        status = ('OpenCode is still starting after 30 seconds. ' if slow else
                  'OpenCode did not start; your message was not delivered. ')
        repair = '\n\nCheck on the host:\n```sh\nagentj doctor\n```\nRestart after repair:\n```sh\nagentj service restart\n```'
    else:
        status = 'OpenCode 启动已超过 30 秒，仍在等待。' if slow else 'OpenCode 未能启动；这条消息尚未交给它。'
        repair = '\n\n在主机检查：\n```sh\nagentj doctor\n```\n修好后重启：\n```sh\nagentj service restart\n```'
    return status + cause + repair


def split_model(m: str | None) -> dict | None:
    if not m or "/" not in m:
        return None
    p, mid = m.split("/", 1)
    return {"providerID": p, "modelID": mid} if p and mid else None


# ---------------------------------------------------------------- what the phone sees for a request
def _diff_sides(diff: str) -> tuple[str, str, bool]:
    old, new, created = [], [], False
    for ln in (diff or "").splitlines():
        if ln.startswith("@@ -0,0 "):
            created = True
        if ln.startswith(("---", "+++", "Index:", "====")):
            continue
        if ln.startswith("-"):
            old.append(ln[1:])
        elif ln.startswith("+"):
            new.append(ln[1:])
    return "\n".join(old), "\n".join(new), created


def to_tool(req: dict, root: str) -> tuple[str, dict]:
    """(tool name, input) in the shape danger.classify / summarize / batch_scope know (Claude Code's names)."""
    from .danger import cred_path
    perm = str(req.get("permission") or "?")
    pats = [p for p in (req.get("patterns") or []) if isinstance(p, str)]
    md = req.get("metadata") if isinstance(req.get("metadata"), dict) else {}

    def absp(p: str) -> str:
        return p if os.path.isabs(p) else os.path.join(root or "/", p)
    if perm == "bash":
        cmd = md.get("command") if isinstance(md.get("command"), str) else "\n".join(pats)
        inp = {"command": cmd}
        if isinstance(md.get("description"), str):
            inp["description"] = md["description"]
        return "Bash", inp
    if perm == "edit":
        files = [absp(p) for p in pats] or ([md["filepath"]] if isinstance(md.get("filepath"), str) else [])
        fp = md.get("filepath") if isinstance(md.get("filepath"), str) else (files[0] if files else "?")
        diff = str(md.get("diff") or "")
        if re.search(r"^\+\+\+ /dev/null\s*$|^\s*\*\*\*\s*Delete File:", diff, re.M):
            # P556 (review S1): a patch that removes a file is a deletion, not an edit — the four kinds must see it
            return "Delete", {"file_path": fp, "files": files or [fp]}
        if len(files) > 1:              # one patch over several files: judge the riskiest path, show every file
            fp = next((f for f in files if cred_path(f)), files[0])
            return "Edit", {"file_path": fp, "old_string": "", "new_string": "files: " + ", ".join(files) + "\n" +
                            str(md.get("diff") or "")}
        old, new, created = _diff_sides(str(md.get("diff") or ""))
        if created:
            return "Write", {"file_path": fp, "content": new}
        return "Edit", {"file_path": fp, "old_string": old, "new_string": new}
    if perm == "read":
        return "Read", {"file_path": absp(pats[0]) if pats else "?"}
    if perm == "webfetch":
        return "WebFetch", {"url": md.get("url") if isinstance(md.get("url"), str) else (pats[0] if pats else "")}
    if perm == "websearch":
        return "WebSearch", {"query": md.get("query") if isinstance(md.get("query"), str) else " ".join(pats)}
    if perm == "external_directory":
        return "ExternalDirectory", {"path": md.get("parentDir") or md.get("filepath") or (pats[0] if pats else "?")}
    if perm == "doom_loop":
        return "DoomLoop", {"tool": md.get("tool"), "input": md.get("input")}
    if perm in ("task", "glob", "grep", "list", "lsp", "skill", "todowrite", "question", "plan_enter", "plan_exit",
                "codesearch"):
        return perm, {"patterns": pats, **{k: v for k, v in md.items() if isinstance(k, str)}}
    # anything else is an MCP / custom tool (OpenCode names them <server>_<tool>): judged by the words of its name
    return "mcp__" + perm, {**{k: v for k, v in md.items() if isinstance(k, str)}, **({"patterns": pats} if pats != ["*"] else {})}


# ---------------------------------------------------------------- a minimal HTTP/1.1 client (loopback only, stdlib)
class HTTPError(Exception):
    pass


class Client:
    def __init__(self, port: int, password: str, user: str = USER):
        self.port = port
        self.auth = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()

    async def _open(self, method: str, path: str, body=None, accept: str = "application/json", written=None):
        data = b"" if body is None else json.dumps(body, ensure_ascii=False).encode()
        r, w = await asyncio.open_connection("127.0.0.1", self.port, limit=LINE_LIMIT)
        head = (f"{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\nAuthorization: {self.auth}\r\n"
                f"Accept: {accept}\r\nConnection: close\r\n")
        if body is not None:
            head += f"Content-Type: application/json\r\nContent-Length: {len(data)}\r\n"
        w.write(head.encode() + b"\r\n" + data)
        await w.drain()
        if written is not None:
            written.set()
        line = await r.readline()
        m = re.match(rb"HTTP/1\.[01] (\d{3})", line)
        if not m:
            w.close()
            raise HTTPError("bad status line")
        headers = {}
        while (h := await r.readline()) not in (b"\r\n", b"\n", b""):
            k, _, v = h.decode("latin-1").partition(":")
            headers[k.strip().lower()] = v.strip()
        return int(m.group(1)), headers, r, w

    @staticmethod
    async def _chunks(headers: dict, r: asyncio.StreamReader):
        if "chunked" in headers.get("transfer-encoding", "").lower():
            while True:
                size = int((await r.readline()).split(b";")[0].strip() or b"0", 16)
                if size == 0:
                    return
                yield await r.readexactly(size)
                await r.readexactly(2)
        elif "content-length" in headers:
            n = int(headers["content-length"])
            if n:
                yield await r.readexactly(n)
        else:
            while chunk := await r.read(65536):
                yield chunk

    async def request(self, method: str, path: str, body=None, timeout: float = REQ_TIMEOUT, written=None):
        async def go():
            status, headers, r, w = await self._open(method, path, body, **({"written": written} if written is not None else {}))
            try:
                buf = b""
                async for c in self._chunks(headers, r):
                    buf += c
                    if len(buf) > MAX_BODY:
                        raise HTTPError("body too large")
            finally:
                w.close()
            try:
                return status, (json.loads(buf) if buf.strip() else None)
            except ValueError:
                return status, None
        try:
            return await asyncio.wait_for(go(), timeout)
        except (asyncio.IncompleteReadError, EOFError) as e:      # the server went away mid-answer
            raise HTTPError("incomplete answer") from e

    async def events(self, idle: float = SSE_IDLE, path: str = "/event"):
        """Yield every SSE event (decoded JSON) until the stream ends; raises HTTPError on a non-200 answer."""
        status, headers, r, w = await asyncio.wait_for(self._open("GET", path, accept="text/event-stream"), REQ_TIMEOUT)
        try:
            if status != 200:
                raise HTTPError(f"event stream {status}")
            buf, data = b"", []
            it = self._chunks(headers, r).__aiter__()
            while True:
                try:
                    c = await asyncio.wait_for(it.__anext__(), idle)
                except StopAsyncIteration:
                    return
                buf += c
                while b"\n" in buf:
                    ln, buf = buf.split(b"\n", 1)
                    ln = ln.rstrip(b"\r")
                    if ln.startswith(b"data:"):
                        data.append(ln[5:].strip())
                    elif not ln and data:
                        try:
                            ev = json.loads(b"\n".join(data))
                        except ValueError:
                            ev = None
                        data = []
                        if isinstance(ev, dict):
                            yield ev
        finally:
            w.close()


def _key(rules) -> list:
    return [(r.get("permission"), r.get("pattern"), r.get("action")) for r in rules if isinstance(r, dict)] \
        if isinstance(rules, list) else []


def ends_with_ours(have, ours) -> bool:
    """Do a session's rules end with exactly ours (so nothing written before them can loosen them)?"""
    h, o = _key(have), _key(ours)
    return bool(o) and h[-len(o):] == o


def _v1_rules(rules) -> list:
    """Either shape → v1 ({permission, pattern, action}); v2 {action, resource, effect} renamed back (agent_opencode2)."""
    if isinstance(rules, list) and rules and isinstance(rules[0], dict) and "resource" in rules[0]:
        from .agent_opencode2 import rules_from_v2
        return rules_from_v2(rules)
    return rules if isinstance(rules, list) else []


def _free_terms(model: str) -> str:
    """P298: the short data-terms phrase of a known OpenCode free model (zh), else ""."""
    from .opencode_free import terms, TERMS_TEXT
    k = terms(model)
    return TERMS_TEXT["zh"][k] if k else ""


_SUB_CACHE: dict = {}        # P426: dir → the last v1 injection computed (a scheduled run's new instance starts with it)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------- the adapter
class OpenCodeAgent(Agent):
    kind = "opencode"
    EVENT_PATH = "/event"          # v2 (agent_opencode2): /api/event
    v2 = False
    SUBAGENTS = True               # P426: per-agent rules + `task <n> allow` (shared classes: False — OpenCode's own rules)
    sub_ok: list | tuple = ()      # (class defaults: never changed in place — instances assign new values)
    sub_why: dict = {}
    sub_inject: dict = {}
    sub_primary = None

    @property
    def children(self) -> dict:
        """P426: child session id → {agent, title} — a sub-agent's requests / questions go to the phone, labelled."""
        return self.__dict__.setdefault("_kids", {})

    def __init__(self, host, cfg: dict, persist: bool = True, research: bool = False):
        if not persist:
            cfg = dict(cfg)
            cfg["_workflow_ceo"] = True
        super().__init__(host, cfg)
        self.proc: asyncio.subprocess.Process | None = None
        self.client: Client | None = None
        self.sid: str | None = None
        self.root = self.cfg.get("dir") or "/"
        self.rules: list[dict] = []
        self.failed_start = False
        self.turn_done: asyncio.Event | None = None
        self.turn_t0 = 0.0
        self.pending: dict[str, asyncio.Future] = {}   # permission id → "gone" (replied elsewhere / process gone)
        self.questions: dict[str, asyncio.Future] = {}  # question id → "gone" (§10.7)
        self.q_sent: dict[str, list] = {}               # question id → the answers serve POSTed (P33-X05 tamper check)
        self.ours: set[str] = set()                     # permission ids serve replied to
        self.emitted: set[str] = set()                  # text part ids already on the phone
        self.connected = asyncio.Event()
        self.err_tail = ""
        self.start_progress = self.turn_progress = time.monotonic()
        self.provider_fail_noted = False
        self.restart_after_turn = False                 # P60: a key / provider failure this turn → end serve after it
        self.connected_at_start: list | None = None     # P60: providers with a key in this serve process (GET /provider)
        self.quiet_exit = False
        self.retry_noted = False
        self.saw_busy = False                           # this turn's session went busy (a stale idle cannot end it)
        self.tasks: set[asyncio.Task] = set()
        self.persist, self.research = persist, research   # a scheduled run: its own session, never stored
        self.agent_rules: list = []
        self.sub_inject: dict = {}                      # P426: agent → the rules injected for it (v1 shape; _SUB_CACHE)
        self.sub_ok: list = []                          # agents seen carrying exactly that injection → `task <n> allow`
        self.sub_why: dict = {}
        self.collect: list | None = None                # run_once: texts are collected, not sent
        self.quiet = False                              # compaction: the summary is not a reply
        self.auth_fp: tuple | None = None               # P59: auth_fingerprint() when the owned serve started
        self.last_provider_fail: dict | None = None     # P59: {reason, http_status, at} of the last session.error (doctor)
        # Configured names are available before any model request can run (including broken keys).
        self._set_models({})
        self.meter(model=self.cur_model(), model_name=self.model_name(self.cur_model()))

    def provider_note(self, reason, pid, fallback, data=None, model=None):
        from .provider_runtime import key_name, request_key
        zh = self.cfg.get("language", "en").startswith("zh")
        if reason in ("login", "no_key") and pid and key_name(pid):
            self._bg(request_key(self.host, pid))
            return ("模型服务 Key 无效或缺失，请在手机密钥卡填写新 Key；也可从菜单「模型和 API Key」打开。存好后再发消息。" if zh else
                    "The provider key is missing or invalid. Update it on the phone key card, or open Models & API keys in the menu, then send again.")
        if reason == "model_missing":   # P423 #146: a ghost model — never the key card; the working ones as buttons
            model, name = clean_line(self.cfg.get("model") or "", 100), clean_line(pid or "", 40)
            pick = bool(self._usable_models())
            return (f"当前选的模型「{model}」在 OpenCode 里用不了：服务商「{name}」在后台没有配置，或这个模型名写错了——不是 Key 失效。"
                    + ("点下面一个能用的模型直接换上，或" if pick else "") + "在手机菜单「模型和 API Key」重选；要继续用它，先在那里把这个服务商配好。"
                    if zh else
                    f"The selected model {model} is not available in OpenCode: its provider {name} is not set up in the background, "
                    "or the model name is wrong — this is not an invalid key. " + ("Tap a working model below, or pick " if pick else "Pick ")
                    + f"one in Models & API keys on the phone; to keep using {name}, set that provider up there first.")
        if reason in ('login', 'no_key'):
            name = clean_line(pid or ('当前服务商' if zh else 'the selected provider'), 40)
            return (f'「{name}」的账户授权或 API Key 不可用。请在手机菜单「模型和 API Key」切换到已配置的服务商；若该账户使用 OAuth，需要先完成服务商的账户授权。' if zh else
                    f'The account authorization or API key for {name} is unavailable. Open Models & API keys on the phone and select a configured provider. If this account uses OAuth, complete the provider account authorization first.')
        parts = fallback.split(" / ", 1)
        text = parts[0] if zh else parts[-1]
        if reason in ("balance", "quota_window", "rate_limit"):
            from .provider_setup import limit_note
            return limit_note(model, clean_line(self.cfg.get("model") or "?", 160), data or {}, self.cfg.get("language", "en"))
        return text

    def _usable_models(self) -> list:
        """Known credential-backed or anonymous free routes, excluding the current failed model.
        This is metadata for the model_missing card, not proof that a key or model call succeeds."""
        cur = self.cfg.get("model")
        return [dict(m) for m in getattr(self, "usable_models", []) if m.get("id") != cur][:40]

    def _offer_models(self) -> None:
        """P423 #146: the existing /model card (one button per working model; a tap is `/model <id>`) right under the
        model_missing notice — the phone's normal switch path, no new entry. Owned serve, the owner's conversation only."""
        models = self._usable_models()
        card, turn = getattr(self.host, "cmd_card", None), getattr(self.host, "cmd_turn", None)
        if not models or not card or not turn or not self.owns_harness() or not getattr(self, "persist", True) or self.cfg.get("_workflow_ceo"):
            return
        zh = self.cfg.get("language", "en").startswith("zh")
        card("model", Result("点一个能用的模型换上（之后的对话都用它）：" if zh else "Tap a working model (used from now on):", "info",
                             models=models), turn=turn(None, "model"))

    def is_down(self) -> bool:
        return self.failed_start

    def _bg(self, coro) -> asyncio.Task:
        t = asyncio.create_task(coro)
        self.tasks.add(t)
        t.add_done_callback(self.tasks.discard)
        t.add_done_callback(lambda t: t.cancelled() or t.exception() is None or         # P192 (B13): failures land in host.log
                            bg.swallowed(bg.host_log(self.host), "bg_task_failed", t.exception(), why="opencode", every=0))
        return t

    def argv(self, port: int) -> list[str]:
        exe = _bin("AGENTJ_OPENCODE_BIN", "opencode") or "opencode"
        return [exe, "serve", "--hostname", "127.0.0.1", "--port", str(port)]

    # ------------------------------------------------ start
    def _sub_mode(self) -> bool:
        """P426: sub-agents get our rules per agent — high-risk mode of Agent J's own serve, not a read-only scheduled run."""
        return bool(self.SUBAGENTS and self.cfg.get("high_risk_warnings", False) and not getattr(self, "research", False))

    def _transport_ready(self) -> bool:
        p = self.proc
        return p is not None and p.returncode is None and self.client is not None

    def readiness(self) -> dict:
        ready = self._transport_ready() and bool(self.sid) and self.connected.is_set() and not self.failed_start
        return {"ready": ready, "code": "adapter_ready" if ready else "native_not_ready"}

    def _retire(self, proc) -> bool:
        """Release only this generation; the durable native conversation pin stays intact."""
        if self.proc is not proc:
            return False
        self.proc = self.client = self.sid = None
        self.connected.clear()
        return True

    async def _ensure_started(self) -> bool:
        if self._transport_ready():
            return True
        p = self.proc
        if p is not None and self.owns_harness():
            await self._kill(p)
        return await self._spawn()

    def _startup_failure(self, phase, reason):
        self.host.st.log("agent_not_ready", agent=self.kind, phase=phase, code=reason,
                         proc_ready=self.proc is not None and self.proc.returncode is None,
                         client_ready=self.client is not None)
        text = ("OpenCode is not ready to open this conversation. Your message was not delivered. Try Reopen AI session, then send again."
                if getattr(self.host, "lang", "zh").startswith("en") else
                "OpenCode 还没准备好，无法打开这段对话；这条消息没有交给它。请点「重开 AI 会话」，完成后再发消息。")
        from .startup_help import guidance
        self.fail_notice(text + "\n" + guidance("opencode", phase, getattr(self.host, "lang", "zh")), recovery=True)

    def _delivery_uncertain(self):
        self.host.st.log("agent_delivery_uncertain", agent=self.kind, phase="prompt", code="transport_error")
        self.fail_notice("Could not confirm whether OpenCode received this message. It will not be sent again automatically. Check the reply before trying again."
                         if getattr(self.host, "lang", "zh").startswith("en") else
                         "暂时无法确认 OpenCode 是否收到了这条消息，系统不会自动重发。请先查看回复，再决定是否重试。")

    async def _spawn(self) -> bool:
        if not _bin("AGENTJ_OPENCODE_BIN", "opencode"):
            self.failed_start = True
            from .startup_help import guidance
            self.fail_notice("后台环境里没找到 OpenCode（opencode 命令）/ OpenCode executable missing in the background environment.\n" +
                             guidance("opencode", "launch", getattr(self.host, "lang", "zh")))
            return False
        from .harness import version_of, opencode_v2
        version = await asyncio.to_thread(version_of, _bin('AGENTJ_OPENCODE_BIN', 'opencode'))
        own = self._own_protocol()
        if own:
            self._switch(version)
        elif opencode_v2(version):                # a shared session attaches to the owner's own server: v1 only for now
            self.failed_start = True
            self.fail_notice('OpenCode v2 的 /api 协议尚未接入 Agent J；这不是模型 key 无效。优先切换 Claude Code，或使用兼容的 OpenCode v1。 / OpenCode v2 API is not supported by this adapter; this is not an invalid model key. Prefer Claude Code or use OpenCode v1.')
            return False
        if self.cfg.get("model") and not split_model(self.cfg["model"]):
            self.failed_start = True
            self.fail_notice("OpenCode 的模型要写成「服务商/模型」，例如 zhipuai/glm-5.3：在Agent J 主机终端重新运行 "
                                   "`agentj agent opencode --dir … --model zhipuai/glm-5.3`。")
            return False
        port, password = free_port(), secrets.token_urlsafe(32)
        argv = await self.authorized_launch_argv(self.argv(port))
        if argv is None:
            self.failed_start = True
            return False
        from .proxy import environment
        from .provider_runtime import fresh_environment
        self.auth_fp = auth_fingerprint()        # P59: what the credentials looked like when this serve started
        from . import fence
        env = opencode_env(environment(fresh_environment(os.environ), self.host.preferences), password)
        if getattr(self, "_sub_mode", bool)() and not self.v2:   # P426: v1 sub-agents get our rules per agent (v2: inherited)
            if not self.sub_inject:
                self.sub_inject = dict(_SUB_CACHE.get(self.cfg.get("dir"), {}))
            if self.sub_inject:
                from .opencode_subagents import content_with
                env["OPENCODE_CONFIG_CONTENT"] = content_with(env["OPENCODE_CONFIG_CONTENT"], self.sub_inject)
        from .provider_setup import overlay
        env["OPENCODE_CONFIG_CONTENT"] = overlay(self.host, env["OPENCODE_CONFIG_CONTENT"])
        if getattr(self, "persist", False) and not self.v2 and not self.cfg.get("_workflow_ceo"):
            from .native_commands import main_config
            env["OPENCODE_CONFIG_CONTENT"] = main_config(env["OPENCODE_CONFIG_CONTENT"], self.cfg)
        if self.cfg.get("_workflow_ceo"):
            env = fence.auxiliary_environment(env, self.kind, extra_keep=("AGENTJ_PERM_SOCK", "AGENTJ_PERM_CLAIM", "AGENTJ_PERM_WAIT", "OPENCODE_SERVER_PASSWORD", "OPENCODE_SERVER_USERNAME", "OPENCODE_CONFIG_CONTENT"), plan_keys=True)   # P348
        if self.cfg.get("fence", True):
            env = fence.launch_environment(env, self.cfg.get("docker", False), self.kind)
        from .static_shell import interpreter_env
        self.approval_env = interpreter_env(env)
        try:
            proc = await asyncio.create_subprocess_exec(*resource_policy.work_argv(argv), cwd=self.cfg["dir"], env=env,
                                                        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.PIPE, limit=LINE_LIMIT, start_new_session=True)
        except OSError as e:
            self.failed_start = True
            self._startup_failure("launch", type(e).__name__)
            return False
        self.proc, self.client, self.sid = proc, None, None
        ready = False
        try:
            self.err_tail, self.quiet_exit, self.failed_start = "", False, False
            self.connected = asyncio.Event()
            self._bg(self._drain_err(proc))
            self.host.st.log("agent_start", agent=self.kind, fence=self.cfg.get("fence", True), session_mode=self.cfg.get("session_mode", "independent"), isolation_effective=getattr(self, "isolation_effective", False))
            listening = None
            slow_noticed = False
            self.start_progress = started = time.monotonic()
            line_task = asyncio.create_task(proc.stdout.readline())
            try:
                while time.monotonic() - started < START_MAX:
                    if not slow_noticed and time.monotonic() - started >= 30:
                        self.host.agent_notice(startup_help(self.err_tail, slow=True, lang=getattr(self.host, "lang", "zh")))
                        slow_noticed = True
                    left = min(START_WAIT - (time.monotonic() - self.start_progress),
                               START_MAX - (time.monotonic() - started))
                    if left <= 0:
                        break
                    done, _ = await asyncio.wait({line_task}, timeout=min(left, 1))
                    if not done:
                        if proc.returncode is not None:
                            break
                        continue
                    line = line_task.result()
                    if not line:
                        break
                    self.start_progress = time.monotonic()
                    m = re.search(rb"listening on http://127\.0\.0\.1:(\d+)", line)
                    if m:
                        listening = int(m.group(1))
                        break
                    line_task = asyncio.create_task(proc.stdout.readline())
            finally:
                if not line_task.done():
                    line_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await line_task
            if listening != port:
                await asyncio.sleep(0.2)
                reason = "process exited" if proc.returncode is not None else "startup limit reached" if time.monotonic() - started >= START_MAX else "no startup progress"
                await self._kill(proc)
                self.host.st.log("agent_startup_timeout", agent=self.kind, reason=reason)
                self.failed_start = True
                self._startup_failure("listen", "startup_failed")
                return False
            self._bg(self._drain(proc.stdout))
            self._bg(self._watch(proc))
            if self.proc is not proc:
                return False
            self.client = Client(port, password)
            try:
                await self._prepare()
                await self.refresh_models()
                self.meter(model=self.cur_model(), model_name=self.model_name(self.cur_model()))
            except (OSError, HTTPError, ValueError, KeyError, TypeError, AttributeError, asyncio.TimeoutError) as e:
                if getattr(proc, "_agentj_requested_stop", False) or any(proc is ended for ended in self.ended) or self.halting:
                    return False  # a requested stop during startup is not a provider/startup failure
                self.host.st.log("agent_prepare_fail", agent=self.kind, reason=type(e).__name__)
                self.quiet_exit = True
                await self._kill(proc)
                self.failed_start = True
                self._startup_failure("prepare", type(e).__name__)
                return False
            self._bg(self._events(proc))
            try:
                await asyncio.wait_for(self.connected.wait(), CONNECT_WAIT)
            except asyncio.TimeoutError:
                self.quiet_exit = True
                await self._kill(proc)
                self.failed_start = True
                self._startup_failure("events", "event_timeout")
                return False
            if self.proc is proc and self.__dict__.pop("_sub_respawn", False) and not self.__dict__.pop("_sub_again", False):
                # P426: the agents' rules ask for another injection than this serve started with (first start, a new agent,
                # the owner's rules changed): start once more with it — `task <n> allow` only after it is seen in place
                self.host.st.log("agent_restart", agent=self.kind, reason="subagent_rules")
                self._sub_again = True
                await self._kill(proc)
                try:
                    return await self._spawn()
                finally:
                    self.__dict__.pop("_sub_again", None)
            if self.proc is proc:
                self._running_version = version
                if getattr(self, "_upgrade_notice", None):
                    v = clean_line(version, 40)
                    self.host.agent_notice(f"已切到 OpenCode {v}" if getattr(self.host, "lang", "zh") == "zh" else f"Now using OpenCode {v}")
                    self._upgrade_notice = None
            ready = self.proc is proc and self._transport_ready()
            return ready
        finally:
            if not ready:
                await self._kill(proc)

    async def _prepare(self) -> None:
        """Where the project root is, the human's own agent ruleset (for their denies), then the session with our rules."""
        st, path = await self.client.request("GET", "/path")
        if st != 200 or not isinstance(path, dict):
            raise HTTPError(f"path {st}")
        self.root = path.get("worktree") if isinstance(path.get("worktree"), str) else self.cfg["dir"]
        st, conf = await self.client.request("GET", "/config")
        from .provider_setup import remember
        remember(self, conf if st == 200 else None)
        default = conf.get("default_agent") if st == 200 and isinstance(conf, dict) else None
        prov = conf.get("provider") if st == 200 and isinstance(conf, dict) else None   # P423 #146: names only (set_up)
        self.config_providers = [k for k in prov if isinstance(k, str)] if isinstance(prov, dict) else []
        st, ags = await self.client.request("GET", "/agent")
        if st != 200 or not isinstance(ags, list):
            raise HTTPError(f"agent {st}")
        names = [default, "build"] if isinstance(default, str) else ["build"]
        ag = next((a for n in names for a in ags if isinstance(a, dict) and a.get("name") == n), None)
        if ag is None:
            raise HTTPError("no primary agent")
        self.agent_rules = ag.get("permission") if isinstance(ag.get("permission"), list) else []
        self.agent_rules = self._sub_plan([a for a in ags if isinstance(a, dict)], ag.get("name"), self.agent_rules)
        self.connected_at_start = None                  # P60: which providers this serve process has a key for (fixed for its life)
        with contextlib.suppress(OSError, HTTPError, asyncio.TimeoutError):
            st, pv = await self.client.request("GET", "/provider")
            if st == 200 and isinstance(pv, dict) and isinstance(pv.get("connected"), list):
                self.connected_at_start = [x for x in pv["connected"] if isinstance(x, str)]
        self.rules = session_rules(self.agent_rules, self.research, self.sub_ok) if self.research or self.cfg.get("high_risk_warnings", False) else list(self.agent_rules)
        await self._attach()

    async def _attach(self) -> None:
        """The conversation agent.json names (our rules re-applied last), or a new one; a scheduled run always a new one."""
        sid = self.host.st.agent_session(self.kind) if self.persist else None
        if sid:
            st, s = await self.client.request("GET", f"/session/{sid}")
            if st == 200 and isinstance(s, dict):
                if not ends_with_ours(s.get("permission"), self.rules):   # (re)apply ours last: what is before them is overridden
                    st, _ = await self.client.request("PATCH", f"/session/{sid}", {"permission": self.rules})
                    if st != 200:
                        raise HTTPError(f"patch {st}")
            else:
                sid = None
                self.host.st.set_agent_session(self.kind, None)
        if not sid:
            title = "Agent J" if self.persist else "Agent J 定时任务"
            st, s = await self.client.request("POST", "/session", {"title": title, "permission": self.rules})
            if st != 200 or not isinstance(s, dict) or not isinstance(s.get("id"), str):
                raise HTTPError(f"session {st}")
            sid = s["id"]
            if self.persist:
                self.record_session(sid)
        st, s = await self.client.request("GET", f"/session/{sid}")
        if st != 200 or not isinstance(s, dict) or not ends_with_ours(s.get("permission"), self.rules):
            raise HTTPError("rules not in place")        # never prompt a session whose last rules are not ours
        self.sid = sid

    # ------------------------------------------------ P426: sub-agents (opencode_subagents)
    def _sub_plan(self, agents: list, primary, primary_rules: list) -> list:
        """Which agents may run as sub-agents (→ `task <n> allow`) and the primary agent's own rules without our injection.
        v2: every agent that can (a child takes our session rules). v1: those whose live rules end with exactly the rules
        we injected for them; asks _spawn for one restart when the injection should change (opencode_subagents)."""
        from . import opencode_subagents as osa
        self.sub_primary = primary
        if not self._sub_mode():
            self.sub_ok, self.sub_why = [], {}
            return primary_rules
        if self.v2:
            self.sub_ok = [] if self.__dict__.get("sub_broken") else [a["name"] for a in agents if osa.capable(a)]
            self.sub_why = {"*": "inherit"} if self.__dict__.get("sub_broken") else {}
            self.host.st.log("opencode_subagents", agent=self.kind, on=len(self.sub_ok), off=len(self.sub_why))
            return primary_rules
        own, _ = osa.split(primary_rules, self.sub_inject.get(primary))
        want, self.sub_ok, self.sub_why = osa.plan(agents, primary, self.sub_inject)
        if want != self.sub_inject:
            self.sub_inject = want
            _SUB_CACHE[self.cfg.get("dir")] = want
            self._sub_respawn = True
        self.host.st.log("opencode_subagents", agent=self.kind, on=len(self.sub_ok), off=len(self.sub_why))
        return own

    async def _sub_agents(self) -> list:
        st, ags = await self.client.request("GET", "/agent")
        return [a for a in ags if isinstance(a, dict)] if st == 200 and isinstance(ags, list) else []

    async def _sub_recheck(self) -> None:
        """Before a turn: the agents' live rules still end with what we injected and still give the same injection (the
        owner edited a config, a v2 serve reloaded it, a new agent appeared) — else restart serve (the turn starts a new one
        that re-plans)."""
        p = self.proc
        if not (self._sub_mode() and not self.v2 and self.client and p is not None and getattr(p, "returncode", 0) is None):
            return
        from .opencode_subagents import plan
        try:
            want, ok, _ = plan(await self._sub_agents(), self.sub_primary, self.sub_inject)
        except (OSError, HTTPError, ValueError, AttributeError, asyncio.TimeoutError):
            return
        if not self.__dict__.pop("_sub_dirty", False) and want == self.sub_inject and set(self.sub_ok) <= set(ok):
            return
        self.host.st.log("agent_restart", agent=self.kind, reason="subagent_rules")
        await self._kill(p)
        if self.proc is p:
            self.proc, self.client = None, None

    def _child_seen(self, cid, parent, agent, title, created: bool, perms=None) -> None:
        """A session whose parent is ours (or one of its children): its requests and questions go to the phone, labelled.
        perms (v2 session.created): the child's own rules — they must end with ours (inherited), else it is stopped."""
        from .opencode_subagents import OC_TITLE
        if not isinstance(cid, str) or cid == self.sid or cid in self.children or self.sid is None:
            return
        if parent != self.sid and parent not in self.children:
            return
        title = title if isinstance(title, str) else ""
        m = OC_TITLE.search(title)
        agent = agent if isinstance(agent, str) and agent else (m.group(1) if m else "")
        src = self._cur_src()                               # S3: the turn that started it (a nested one: its parent's too)
        if parent in self.children:
            src = provenance.stricter(src, self.children[parent].get("src"))
        self.children[cid] = {"agent": clean_line(agent, 64), "title": clean_line(OC_TITLE.sub("", title), 80), "src": src}
        while len(self.children) > 64:
            old = self.children.pop(next(iter(self.children)))
            self.gone_src = provenance.stricter(getattr(self, "gone_src", None), old.get("src"))
        if created and self._sub_mode() and self.client:
            if not self.v2:
                self._bg(self._sub_child(cid, agent))
            elif self.rules and not ends_with_ours(_v1_rules(perms), _v1_rules(self.rules)):
                self.sub_broken = True                      # this OpenCode does not hand our rules down: off from now on
                self._bg(self._sub_stop(cid))

    async def _sub_child(self, cid: str, agent: str) -> None:
        """A sub-agent just started: its agent must be one we verified, and still carry our rules — else it is stopped
        (and serve restarts before the next turn). A short race by nature; the session's `task` rule is the gate."""
        from .opencode_subagents import plan
        with contextlib.suppress(OSError, HTTPError, ValueError, AttributeError, asyncio.TimeoutError):
            want, ok, _ = plan(await self._sub_agents(), self.sub_primary, self.sub_inject)
            if agent in ok and agent in self.sub_ok and want.get(agent) == self.sub_inject.get(agent):
                return
            self._sub_dirty = True
            await self._sub_stop(cid)

    async def _sub_stop(self, cid: str) -> None:
        self.host.st.log("opencode_subagent_stopped", agent=self.kind)
        self.sub_ok = []
        with contextlib.suppress(OSError, HTTPError, AttributeError, asyncio.TimeoutError):
            await self._child_stop(cid)
        self.host.agent_notice("OpenCode 的子 Agent 规则没有核实（配置刚被改过，或不是允许的子 Agent）：为了安全，已停下这个子 Agent。"
                               " / An OpenCode sub-agent's rules could not be verified: it was stopped.")

    async def _child_stop(self, cid: str) -> None:
        await self.client.request("POST", f"/session/{cid}/abort", None, timeout=3)

    def _child_done(self, cid: str) -> None:
        """P426 (4): a sub-agent finished while no turn of ours runs (a background one) — OpenCode wakes the main session
        itself; its requests still ask the phone and its text still arrives. One quiet line says so."""
        c = self.children.get(cid)
        if c is not None:      # S3: what OpenCode runs in the main session outside our turns now also carries its source
            self.out_src = provenance.stricter(self.out_src, c.get("src"))
        if c is None or c.get("done") or (self.turn_done is not None and not self.turn_done.is_set()):
            return
        c["done"] = True
        who = " · ".join(x for x in (c.get("agent"), c.get("title")) if x) or "?"
        self.host.agent_notice(f"子 Agent 完成：{who}。主 Agent 会接着处理，回复照常发到这里。"
                               f" / Sub-agent finished: {who}. The main Agent continues; its reply arrives here.")

    out_src_known = False
    out_src = provenance.normalize(None)     # S3: what the main session runs outside our turns: the last turn we started + sub-agents that woke it

    def _note_turn(self) -> None:
        """A turn of ours starts: the main session now runs this Send's source (None = unknown, never the owner)."""
        self.out_src_known = isinstance(getattr(getattr(self, "cur_send", None), "source", None), dict)
        self.out_src = provenance.normalize(getattr(getattr(self, "cur_send", None), "source", None))

    def _cur_src(self):
        send = getattr(self, "cur_send", None)
        return provenance.normalize(getattr(send, "source", None) if send is not None else self.out_src)

    def _req_src(self, sid):
        """P426 (S3): the source a request / question is judged by. A sub-agent's: the turn that started it, never the
        current one's alone (a friend's turn ended, cur_send is None — not the owner). The main session outside our turns
        (OpenCode woke it after a sub-agent finished): out_src. A session we no longer track: the strictest we dropped.
        None = this turn's Send decides, as before."""
        c = self.children.get(sid) if isinstance(sid, str) else None
        if c is not None and "src" in c:
            return provenance.normalize(c["src"])
        if c is None and sid in (self.sid, None):
            return provenance.normalize(self.out_src) if getattr(self, "cur_send", None) is None else None
        return provenance.stricter(self._cur_src(), getattr(self, "gone_src", None))

    def _src_ctx(self, sid):
        src = self._req_src(sid)
        return contextlib.nullcontext() if src is None else provenance.request_source(src)

    def _refused(self) -> bool:
        """Inside _src_ctx: a source that may not ask the owner anything (a group member, a guest, unknown) — B1."""
        a = getattr(self.host, "agent", None)
        a = a if a is not None else self
        return provenance.untrusted_turn(a) and not provenance.delegated_turn(a)

    def subagent_state(self) -> dict:
        """settings / doctor / status: on (names) · native (OpenCode's own rules: default mode, shared) · off (why)."""
        if getattr(self, "research", False):
            return {"mode": "off", "why": "research"}
        if not self.SUBAGENTS:       # shared: v1's plugin guards every session; v2's ask rules keep `subagent * deny`
            return {"mode": "off", "why": "shared"} if self.v2 and self.cfg.get("high_risk_warnings", False) else {"mode": "native"}
        if not self.cfg.get("high_risk_warnings", False):
            return {"mode": "native"}
        return {"mode": "on", "names": list(self.sub_ok)} if self.sub_ok else {"mode": "off", "why": "none"}

    # ------------------------------------------------ the process
    def _last_err(self) -> str:
        t = self.err_tail.strip()
        return clean(t.splitlines()[-1], 200) if t else ""

    async def _drain(self, stream) -> None:
        with contextlib.suppress(Exception):
            while await stream.read(65536):
                pass

    async def _drain_err(self, proc) -> None:
        with contextlib.suppress(Exception):
            while line := await proc.stderr.readline():
                if self.proc is not proc:
                    return
                self.start_progress = time.monotonic()
                self.err_tail = (self.err_tail + line.decode("utf-8", "replace"))[-2000:]

    async def _watch(self, proc) -> None:
        code = await proc.wait()
        if self.proc is proc or self.proc is None:        # P160: its sub-agents ended with it (not a newer serve's)
            self._busy(None)
        self.host.st.log("agent_exit", agent=self.kind, status=code)
        self._retire(proc)
        quiet = proc in self.ended
        self.ended.discard(proc)
        if self.proc is not None and self.proc is not proc:
            return  # an obsolete watcher cannot finish a newer turn
        tp = self.turn_proc
        if tp is not None and tp is not proc and tp.returncode is None:
            return                                        # an older process: the running turn is not its business
        for f in list(self.pending.values()):
            if not f.done():
                f.set_result(True)
        if self.turn_done and not self.turn_done.is_set():
            if not quiet:
                last = self._last_err()
                self.fail_notice(f"OpenCode 退出了（{code}）" + (f"：{last}" if last else ""))
            self.turn_done.set()
        elif not quiet:
            self.host.agent_notice(f"OpenCode 退出了（{code}）：下一条消息会重新启动它。")

    async def _kill(self, p) -> None:
        if self.proc is p:
            self.quiet_exit = True
        self.ended.add(p)
        self._retire(p)
        if p.returncode is None:
            _signal_tree(p, signal.SIGTERM)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(p.wait(), 5)
            _signal_tree(p, signal.SIGKILL)

    # ------------------------------------------------ the event stream
    def busy_scope(self) -> str:
        return ("shared-" if self.__class__.__name__.startswith(("Shared", "Owner")) else "") + "opencode"

    def _busy(self, ev) -> None:
        """P160 (§22): this session's sub-agents (v1 `task` tool parts; v2 child sessions) → the phone's robots
        (busy.py), like Claude's. A scheduled run is the workflow's own item (tasks.py); None = serve ended."""
        b = getattr(self.host, "busy", None)
        if self.cfg.get("_workflow_ceo") and isinstance(ev, dict):
            properties = ev.get("properties") or {}
            info = properties.get("info") if isinstance(properties, dict) else None
            if ev.get("type") == "message.updated" and isinstance(info, dict) and info.get("role") == "assistant":
                model, provider = info.get("modelID"), info.get("providerID")
                if isinstance(model, str):
                    self.busy_selected = {"model": provider + "/" + model if isinstance(provider, str) else model}
        if b is None or self.cfg.get("_workflow_ceo") or getattr(self, "research", False):
            return
        with contextlib.suppress(Exception):
            b.set_context(self.busy_scope(), harness="opencode", cwd=self.cfg.get("dir"), pid=getattr(getattr(self, "proc", None), "pid", None))
            if ev is None:
                b.session_gone(self.busy_scope())
            else:      # P426: a sub-agent that ends while our turn runs is foreground — no 「后台活已结束」 wake
                td = getattr(self, "turn_done", None)
                on = td is not None and not td.is_set()
                (b.opencode_event if self.EVENT_PATH == "/event" else b.opencode2_event)(ev, self.sid, self.busy_scope(), turn_on=on)

    def _sync_owner(self) -> tuple:
        return self.proc, self.client, self.sid, self.turn_done

    def _sync_current(self, owner: tuple) -> bool:
        p, c, sid, turn = owner
        return self.proc is p and self.client is c and self.sid == sid and self.turn_done is turn

    async def _events(self, proc) -> None:
        delay = 0.5
        first = True
        while self.proc is proc and proc.returncode is None and self.client:
            client = self.client
            try:
                async for ev in client.events(path=self.EVENT_PATH):
                    if self.proc is not proc or self.client is not client:
                        return
                    if not self.connected.is_set():
                        self.connected.set()
                    if not first:
                        first = True
                        self.host.st.log("agent_events", agent=self.kind, status="reconnected")
                        owner = self._sync_owner()
                        await self._resync()
                        if self.proc is not proc or self.client is not client:
                            return
                        if not self._sync_current(owner):
                            continue  # discard this old event, keep the live stream for the new turn
                    delay = 0.5
                    self._busy(ev)
                    self.on_event(ev)
            except HTTPError as e:
                self.host.st.log("agent_events", agent=self.kind, status="refused", reason=str(e)[:40])
            except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError, ValueError):
                pass
            finally:
                if self.proc is proc and self.client is client:
                    self.connected.clear()
            if self.proc is not proc or self.client is not client or proc.returncode is not None:
                return
            first = False
            self.host.st.log("agent_events", agent=self.kind, status="lost")
            await asyncio.sleep(delay)
            delay = min(delay * 2, 5)

    async def _resync(self) -> None:
        """After the stream came back: requests and text that went by while it was down."""
        owner = self._sync_owner()
        c = self.client
        if not c:
            return
        with contextlib.suppress(OSError, HTTPError, asyncio.TimeoutError):
            st, reqs = await c.request("GET", "/permission")
            if not self._sync_current(owner):
                return
            live = {r.get("id") for r in reqs} if st == 200 and isinstance(reqs, list) else None
            if live is not None:
                for rid, f in list(self.pending.items()):
                    if rid not in live and not f.done():
                        f.set_result(True)
                for r in reqs:
                    if isinstance(r, dict):
                        self._on_perm(r)
            await self._catch_up()
            if not self._sync_current(owner):
                return
            st, busy = await c.request("GET", "/session/status")
            if not self._sync_current(owner):
                return
            if st == 200 and isinstance(busy, dict) and self.turn_done and not self.turn_done.is_set():
                s = busy.get(self.sid) if self.sid else None
                if not isinstance(s, dict) or s.get("type") == "idle":
                    self._turn_end()

    async def _catch_up(self) -> None:
        if not (self.client and self.sid and self.turn_done):
            return
        owner = self._sync_owner()
        st, msgs = await self.client.request("GET", f"/session/{self.sid}/message")
        if not self._sync_current(owner):
            return
        if st != 200 or not isinstance(msgs, list):
            return
        for m in msgs:
            info = m.get("info") if isinstance(m, dict) else None
            if not isinstance(info, dict) or info.get("role") != "assistant":
                continue
            if ((info.get("time") or {}).get("created") or 0) < self.turn_t0 - 5000:
                continue
            for part in m.get("parts") or []:
                self._text_part(part)

    def _text_part(self, part) -> None:
        if not isinstance(part, dict) or part.get("type") != "text" or part.get("sessionID") != self.sid:
            return
        if part.get("synthetic") or part.get("ignored") or not (part.get("time") or {}).get("end"):
            return
        pid = part.get("id")
        if not isinstance(pid, str) or pid in self.emitted:
            return
        self.emitted.add(pid)
        text = strip_reasoning(part.get("text"))     # P198: a model's </think> reasoning is not the reply
        if isinstance(text, str) and text.strip() and not self.quiet:
            if self.collect is not None:
                self.collect.append(text)
            else:
                self.host.agent_text(text)

    def on_event(self, ev: dict) -> None:
        t = ev.get("type")
        p = ev.get("properties") if isinstance(ev.get("properties"), dict) else {}
        if self.sid is not None and self.sid in (p.get("sessionID"), (p.get("part") or {}).get("sessionID"),
                                                 (p.get("info") or {}).get("sessionID") if isinstance(p.get("info"), dict) else None):
            self.touch()                   # P219: progress on our session (stall watch)
        if t == "message.part.updated" and (p.get("part") or {}).get("sessionID") == self.sid:
            self.turn_progress = time.monotonic()
        if t in ("session.created", "session.updated") and isinstance(p.get("info"), dict):    # P426: a sub-agent's session
            i = p["info"]
            self._child_seen(i.get("id"), i.get("parentID"), i.get("agent"), i.get("title"), t == "session.created")
        if p.get("sessionID") in self.children:
            st_ = (p.get("status") or {}).get("type") if isinstance(p.get("status"), dict) else None
            if t == "session.idle" or st_ == "idle":
                self._child_done(p["sessionID"])
            elif st_ == "busy":
                self.children[p["sessionID"]].pop("done", None)
        if t == "session.error" and p.get("sessionID") in self.children:
            from .provider_setup import error_model, limit_note
            err = p.get("error") if isinstance(p.get("error"), dict) else {}
            data = err.get("data") if isinstance(err.get("data"), dict) else {}
            reason = provider_error_reason(str(err.get("name") or ""), data)
            if reason in ("balance", "quota_window", "rate_limit"):
                self.host.agent_notice(limit_note(error_model(data), self.cfg.get("model") or "?", data, self.cfg.get("language", "en")))
            return
        if t == "permission.asked":
            self._on_perm(p)
        elif t == "permission.replied":
            rid = p.get("requestID")
            f = self.pending.get(rid)
            if f and not f.done():
                f.set_result(True)
            if p.get("reply") in ("once", "always") and rid not in self.ours:
                self._tamper("foreign_reply")
        elif t == "message.part.updated":
            self._tool_dir(p.get("part"))
            self._text_part(p.get("part"))
        elif t == "session.error" and p.get("sessionID") in (self.sid, None):
            err = p.get("error") if isinstance(p.get("error"), dict) else {}
            if err.get("name") != "MessageAbortedError":
                name = str(err.get("name") or "")
                data = err.get("data") if isinstance(err.get("data"), dict) else {}
                code = data.get("statusCode")
                code = code if type(code) is int and 100 <= code <= 599 else None
                from .provider_setup import error_model
                failed_model = error_model(data)
                sel = split_model(failed_model or self.cfg.get("model"))
                pid = sel["providerID"] if sel else None
                set_up = set_up_providers(getattr(self, "config_providers", []) + list(self.connected_at_start or []))
                reason = provider_error_reason(name, data, self.connected_at_start, pid, set_up)
                if failed_model and failed_model != self.cfg.get("model") and reason in ("balance", "quota_window", "rate_limit"):
                    self.host.agent_notice(self.provider_note(reason, pid, PROVIDER_NOTES[reason], data, failed_model))
                    return
                self.host.st.log("agent_provider_fail", agent=self.kind, reason=reason, http_status=code)
                self.last_provider_fail = {"reason": reason, "http_status": code, "at": int(time.time())}
                if code == 401 and not self.cfg.get("_workflow_ceo"):
                    from . import ark_plan_fix
                    if pid in ark_plan_fix.PLANS:
                        self._bg(ark_plan_fix.notify(self.host, self.cfg.get("model")))
                ar = getattr(self.host, "auth_rescue", None)
                cls = {"login": "login", "no_key": "login", "balance": "balance", "quota_window": "rate_limit",
                       "rate_limit": "rate_limit"}.get(reason)
                if ar is not None and cls and not self.cfg.get("_workflow_ceo"):
                    try:   # P258: the provider classifier decided; OpenCode's rescue is the provider key card
                        ar.signal("opencode", cls)
                    except Exception:  # noqa: BLE001
                        pass
                if reason in RESTART_AFTER and self.owns_harness():
                    self.restart_after_turn = True        # P60: the next message gets a fresh serve (current keys)
                notes = PROVIDER_NOTES if self.owns_harness() else {**PROVIDER_NOTES, "login": PROVIDER_LOGIN_ATTACHED}
                notes = {**notes, "no_key": notes["no_key"].replace("{provider}", clean_line(pid or "该服务商", 40))}
                if not self.provider_fail_noted:
                    self.provider_fail_noted = True
                    if code == 401 and pid in ('ark-agent-plan', 'ark-coding-plan') and not self.cfg.get('_workflow_ceo'):
                        self.fail_notice(ark_plan_fix.checking_note(self.cfg.get('model'), self.cfg.get('language','en'), data))
                    else:
                        self.fail_notice(self.provider_note(reason, pid, notes[reason], data, failed_model))
                    if reason == "model_missing":
                        self._offer_models()
        elif t == "session.status" and p.get("sessionID") == self.sid:
            s = p.get("status") if isinstance(p.get("status"), dict) else {}
            if s.get("type") in ("busy", "retry"):
                self.saw_busy = True
            if s.get("type") == "retry" and not self.retry_noted:
                self.retry_noted = True
                self.host.agent_notice("模型服务出错，OpenCode 正在重试" + (f"：{clean(s['message'], 200)}"
                                                                     if isinstance(s.get("message"), str) else "。"))
            elif s.get("type") == "idle" and self.saw_busy:
                self._turn_end()
        elif t == "session.idle" and p.get("sessionID") == self.sid and self.saw_busy:
            self._turn_end()
        elif t == "session.updated" and p.get("sessionID") == self.sid:
            info = p.get("info") if isinstance(p.get("info"), dict) else {}
            if "permission" in info and self.rules and not ends_with_ours(info.get("permission"), self.rules):
                self._tamper("session_rules")
        elif t == "question.asked" and isinstance(p.get("id"), str) and self.client:   # §10.7: a card on the phone
            sid = p.get("sessionID")
            if p["id"] in self.questions:
                return
            if sid not in (self.sid, None) and sid not in self.children:     # P426: a sub-agent's question, if it is ours
                self._bg(self._adopt(sid, lambda: self._bg(self._question(p["id"], p.get("questions"), sid))))
                return
            self._bg(self._question(p["id"], p.get("questions"), sid))
        elif t in ("question.replied", "question.rejected"):
            rid = p.get("requestID")
            f = self.questions.get(rid)
            if f and not f.done():
                f.set_result(True)
            if t == "question.replied":
                # P33-X05: the question endpoint takes the server password, which the Agent's own tool processes inherit.
                # An answer serve did not POST (or different from the one it POSTed, which the human signed) means
                # something else answered for the human: OpenCode is stopped at once, like a foreign permission reply.
                mine = self.q_sent.pop(rid, None)
                if mine is None or ("answers" in p and p.get("answers") != mine):
                    self._tamper("foreign_question_reply")
            else:
                self.q_sent.pop(rid, None)

    async def _session_info(self, sid: str) -> dict:
        st, s = await self.client.request("GET", f"/session/{sid}")
        return s if st == 200 and isinstance(s, dict) else {}

    async def _adopt(self, sid, then) -> None:
        """P426: a request from a session we have not seen created (the stream was down): ours if its parent is."""
        if not isinstance(sid, str) or not self.client:
            return
        with contextlib.suppress(OSError, HTTPError, ValueError, asyncio.TimeoutError):
            i = await self._session_info(sid)
            self._child_seen(sid, i.get("parentID"), i.get("agent"), i.get("title"), False)
        if sid in self.children:
            then()

    def _label(self, qs: list | None, sid) -> list | None:
        """P426: a sub-agent's question card names it (the first question's last line)."""
        from .opencode_subagents import origin
        o = origin(self.children.get(sid), getattr(self.host, "lang", "zh"))
        if o and qs and isinstance(qs[0], dict) and isinstance(qs[0].get("question"), str):
            qs = [{**qs[0], "question": qs[0]["question"] + "\n" + o}] + list(qs[1:])
        return qs

    async def _question(self, qid: str, qs, sid=None) -> None:
        """question.asked → one card; the answer = the picked labels per question (`reply`); cancel / timeout / anything the
        card cannot show → `reject` (the model is told nobody chose, and asks in text)."""
        from .approvals import norm_questions
        gone = asyncio.get_running_loop().create_future()
        self.questions[qid] = gone
        client = self.client
        body, path = {}, f"/question/{qid}/reject"
        try:
            raw = [{"question": q.get("question"), "header": q.get("header") or "", "multiSelect": bool(q.get("multiple")),
                    "options": q.get("options")} for q in qs] if isinstance(qs, list) else None
            card = norm_questions(self._label(raw, sid)) if raw and not self.research else None
            with self._src_ctx(sid):
                if card is not None and self._req_src(sid) is not None and self._refused():
                    card = None                         # S3: a group member's sub-agent asks the owner nothing
                if card is not None:
                    outcome, picks = await self.host.question(card, gone, task=getattr(self.host, "task_label", None))
                    if outcome == "answer" and picks:
                        body = {"answers": [[p] if isinstance(p, str) else [q["o"][n - 1]["l"] for n in p] for q, p in zip(card, picks)]}
                        path = f"/question/{qid}/reply"
        except Exception:  # noqa: BLE001 — fail closed: rejected
            body, path = {}, f"/question/{qid}/reject"
        finally:
            self.questions.pop(qid, None)
        if gone.done() or not client:
            return
        if path.endswith("/reply"):
            self.q_sent[qid] = body["answers"]          # before the POST: its question.replied must match exactly this
        with contextlib.suppress(OSError, HTTPError, asyncio.TimeoutError):
            await client.request("POST", path, body)

    @staticmethod
    async def _quiet(coro) -> None:
        with contextlib.suppress(Exception):
            await coro

    def _turn_end(self) -> None:
        if self.turn_done and not self.turn_done.is_set():
            for f in list(self.pending.values()):    # a request OpenCode dropped (abort) must not keep its card open
                if not f.done():
                    f.set_result(True)
            self.turn_done.set()

    def _tamper(self, why: str) -> None:
        p = self.proc
        self.host.st.log("agent_tamper", agent=self.kind, reason=why)
        self.host.agent_notice("有程序绕过手机操作了 OpenCode（直接批准请求、替你回答问题或改了会话规则）：为了安全，已停止 OpenCode。"
                               "下一条消息会重新启动它；如果不是你做的，检查这台Agent J 主机上 Agent 最近运行过的程序。")
        if p:
            self.quiet_exit = True
            self._bg(self._kill(p))

    # ------------------------------------------------ approvals
    def _on_perm(self, req: dict) -> None:
        self.touch()
        rid = req.get("id")
        if not isinstance(rid, str) or not rid.startswith("per") or rid in self.pending or rid in self.ours:
            return
        gone = asyncio.get_running_loop().create_future()
        self.pending[rid] = gone
        if isinstance(req.get("sessionID"), str):
            self.__dict__.setdefault("perm_sid", {})[rid] = req["sessionID"]    # P426: answered on its own session (v2)
        self._bg(self._decide(rid, req, gone))

    def _tool_dir(self, part) -> None:
        """P198 (P164 G): a bash call's `workdir` (OpenCode runs it there, not in the project root), by call id — its
        permission request carries only the command, so the card / the goal tiers would resolve relative paths wrongly."""
        if not isinstance(part, dict) or part.get("type") != "tool" or not isinstance(part.get("callID"), str):
            return
        st = part.get("state") if isinstance(part.get("state"), dict) else {}
        inp = st.get("input") if isinstance(st.get("input"), dict) else {}
        wd = inp.get("workdir")
        if isinstance(wd, str) and wd:
            dirs = self.__dict__.setdefault("tool_dirs", {})
            if len(dirs) >= 256:
                dirs.pop(next(iter(dirs)))
            dirs[part["callID"]] = wd if os.path.isabs(wd) else os.path.join(self.cfg.get("dir") or "/", wd)

    async def _decide(self, rid: str, req: dict, gone: asyncio.Future) -> None:
        reply, msg = "reject", "已拒绝。"
        sid = getattr(self, "perm_sid", {}).pop(rid, None)
        try:
            tool, inp = to_tool(req, self.root)
            call = (req.get("tool") or {}).get("callID") if isinstance(req.get("tool"), dict) else None
            wd = getattr(self, "tool_dirs", {}).get(call) if isinstance(call, str) else None
            if wd and tool == "Bash" and isinstance(inp.get("command"), str):
                from .approval_jev import with_cwd        # P198: where OpenCode really runs it (its workdir)
                inp = {**inp, "command": with_cwd(inp["command"], wd, self.cfg.get("dir"))}
            name = clean_line(tool, 64) or "?"
            from .approval_jev import low_risk_pass       # P198: P157 layer 1 — low risk runs without a card, like Claude
            from .opencode_subagents import origin       # P426: a sub-agent's card says so (P172's last line)
            o = origin(self.children.get(sid), getattr(self.host, "lang", "zh"))
            with self._src_ctx(sid):       # S3: a sub-agent / OpenCode's own wake turn is judged by its source
                ans = ({"behavior": "allow"} if low_risk_pass(self.host, self.cfg, name, inp)
                       else await self.host.ask(name, inp, gone, **({"origin": o} if o else {}),
                                         **({"background": "task"} if self.cfg.get("_workflow_ceo") else {})))   # P552
            if ans.get("behavior") == "allow":
                reply = "once"
            else:
                msg = ans.get("message") if isinstance(ans.get("message"), str) else msg
        except Exception:  # noqa: BLE001 — fail closed: an adapter bug is a reject, never an allow
            reply = "reject"
        finally:
            self.pending.pop(rid, None)
        if gone.done() or not self.client:
            return                                        # replied elsewhere (a reject of the same session) or gone
        self.ours.add(rid)
        with contextlib.suppress(OSError, HTTPError, asyncio.TimeoutError):
            await self._reply_permission(rid, reply, msg, sid)

    async def _reply_permission(self, rid: str, reply: str, msg: str, sid=None) -> None:
        body = {"reply": reply} if reply == "once" else {"reply": "reject", "message": msg}
        await self.client.request("POST", f"/permission/{rid}/reply", body)

    # ------------------------------------------------ turns
    async def _session_ok(self) -> bool:
        """The conversation agent.json names is the one loaded (after /clear or 「撤销清空」 it is not)."""
        if not self._transport_ready():
            self._startup_failure("session", "native_not_ready")
            return False
        if self.sid is not None and (not self.persist or self.sid == self.host.st.agent_session(self.kind)):
            return True
        try:
            await self._attach()
            return True
        except (OSError, HTTPError, ValueError, KeyError, TypeError, AttributeError, asyncio.TimeoutError) as e:
            self._startup_failure("session", type(e).__name__)
            return False

    def owns_harness(self) -> bool:
        """Independent mode, or a shared session whose `opencode serve` Agent J started itself (shared.py owned_server)."""
        return getattr(self, "owned_server", self.cfg.get("session_mode") != "shared")

    async def _creds_check(self) -> None:
        """P59 (A167): `opencode auth login` (or a config edit) since our serve started → restart it before this turn; the
        next spawn re-attaches the same session id (the conversation lives in OpenCode's own store). Owned serve only."""
        await self._sub_recheck()
        p = self.proc
        if (p is None or getattr(p, "returncode", 0) is not None or not hasattr(p, "pid") or self.auth_fp is None
                or not self.owns_harness()):
            return
        if await asyncio.to_thread(auth_fingerprint) == self.auth_fp:
            return
        self.host.st.log("agent_restart", agent=self.kind, reason="credentials_changed")
        await self._kill(p)
        if self.proc is p:
            self.proc, self.client = None, None

    # ------------------------------------------------ P60: v1 or v2 — decided before any v1-only code runs
    def _own_protocol(self) -> bool:
        """This adapter (not a shared session's subclass, which attaches to the owner's own server: v1 only for now)."""
        return type(self) is OpenCodeAgent or self.v2

    def _switch(self, version) -> bool:
        """The class for the installed OpenCode (v2 = agent_opencode2: its own /api protocol). True when it changed."""
        from .harness import opencode_v2
        if opencode_v2(version) == self.v2:
            return False
        from .agent_opencode2 import OpenCodeV2Agent
        self.__class__ = OpenCodeV2Agent if opencode_v2(version) else OpenCodeAgent
        self.host.st.log("agent_protocol", agent=self.kind, version="v2" if self.v2 else "v1")
        return True

    async def _protocol(self) -> bool:
        """Ask `opencode --version` once per executable (path + mtime: an upgrade in place is seen) and switch the class."""
        exe = _bin("AGENTJ_OPENCODE_BIN", "opencode")
        if not exe or not self._own_protocol():
            return False
        try:
            st = os.stat(exe)
            key = (os.path.realpath(exe), st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
        except OSError:
            return False
        if getattr(self, "_proto_key", None) == key:
            return False
        from .harness import version_of
        version = await asyncio.to_thread(version_of, exe)
        if not version:
            return False  # A failed version probe must not select v1 or end a healthy process.
        self._proto_key = key
        self._installed_version = version
        proc = getattr(self, "proc", None)
        if proc is not None and version != getattr(self, "_running_version", version):
            # Called by the serial command/turn queue before sending the next prompt.
            await self._kill(proc)
            self.proc = self.client = None
            self.sid = None  # The durable session id stays intact; _prepare resumes it.
            self._upgrade_notice = version
            self._switch(version)
            return True
        return self._switch(version)

    async def reload_identity(self) -> None:
        # P495: v1 native commands use a child-only primary-agent prompt, fixed at spawn.
        # Refresh between turns; attached owner servers are never restarted by this path.
        if not self.v2 and self.persist and self._own_protocol() and self.proc is not None:
            await self._kill(self.proc)
            self.proc = self.client = None
            self.sid = None

    async def command(self, name: str, arg: str) -> Result:
        from .command_queries import readonly
        # A live read-only query uses the running protocol; probing an upgraded binary
        # here could terminate the process that still owns the current turn.
        if not (self.proc is not None and readonly(name, arg)):
            await self._protocol()
        return await super().command(name, arg)

    async def turn(self, text: str) -> None:
        self._note_turn()
        if await self._protocol():
            return await self.turn(text)          # now the other class's turn
        await self._creds_check()
        for attempt in range(2):
            if not await self._ensure_started():
                return
            if self.v2:                           # switched while starting (upgraded in place)
                return await self.turn(text)
            if not await self._session_ok():
                return
            self.turn_done, self.turn_proc = asyncio.Event(), self.proc
            self.turn_t0 = time.time() * 1000
            self.turn_progress = time.monotonic()
            self.provider_fail_noted = self.restart_after_turn = False
            self.retry_noted = self.saw_busy = False
            from . import main_identity
            body = turn_body(text, self.cfg, True) if self.persist and not self.cfg.get("_workflow_ceo") else \
                {"parts": [{"type": "text", "text": text}]}
            m = split_model(self.cfg.get("model"))
            if m:
                body["model"] = m
            box = {}
            client, sid = self.client, self.sid
            from .native_commands import opencode_request
            try:
                if self.persist and not self.cfg.get("_workflow_ceo") and (parsed := slash.parse(text)) and parsed[0] in ("init", "review"):
                    from .native_commands import NATIVE_AGENT
                    native = (f"/session/{sid}/command", {"command": parsed[0], "arguments": parsed[1], "agent": NATIVE_AGENT})
                    if self.cfg.get("model"):
                        native[1]["model"] = self.cfg["model"]
                else:
                    native = opencode_request(text, sid, self.cfg.get("model"), False)
            except ValueError as e:
                self.fail_notice(str(e))
                return
            path, request_body = native or (f"/session/{sid}/prompt_async", body)

            async def post():
                box["st"], _ = await client.request("POST", path, request_body, **({"timeout": 600} if native else {}))
                if box["st"] in (200, 204):             # §10.2: prompt_async accepted = delivered
                    return True
                # 4xx: OpenCode refused it (never delivered); anything else may have been taken (P33-X08)
                return False if isinstance(box["st"], int) and 400 <= box["st"] < 500 else UNCERTAIN
            try:
                if native:
                    from .native_commands import request_v1
                    st, _ = await request_v1(self, client, path, request_body)
                else:
                    await self.deliver(post)
                    st = box["st"]
            except (OSError, HTTPError, AttributeError, asyncio.TimeoutError):
                if self.proc:
                    await self._kill(self.proc)
                self._delivery_uncertain()
                return  # the native request may already have executed; never replay it
            if st == 404 and not attempt and not native:                 # the conversation is gone: a fresh one
                if self.persist:
                    self.host.st.set_agent_session(self.kind, None)
                await self._kill(self.proc)
                continue
            if st not in (200, 204):
                self.fail_notice(f"OpenCode 没有接这条消息（HTTP {st}）。")
                return
            if self.persist and not self.cfg.get("_workflow_ceo") and not native:
                main_identity.audit(self.cfg, self.kind, self.host.st, self.sid)
            await self._wait_turn()
            if not self.provider_fail_noted:
                self.last_provider_fail = None            # P59: a clean turn clears doctor's "last turn failed"
            if self.restart_after_turn and self.proc:     # P60: a key / provider failure — the next message gets a fresh serve
                self.restart_after_turn = False
                self.host.st.log("agent_restart", agent=self.kind, reason="provider_failure")
                await self._kill(self.proc)
                self.proc = self.client = None
                return
            with contextlib.suppress(OSError, HTTPError, asyncio.TimeoutError):
                await self._catch_up()                    # a text part whose end event never came
            if self.persist and self.collect is None:
                context_task = self.refresh_usage()
                if self.cfg.get('session_mode') != 'shared':
                    await context_task  # compactprep reads this meter before decorating the next turn
            return

    async def context_meter(self, include_quota: bool = True) -> None:
        """Measured tokens with native/config limits or checked provider/model policy."""
        selected = self.cur_model()
        info = await self._last_assistant()
        used = self._ctx(info)
        m = self._model_of(info)
        from .provider_runtime import configured, probe
        from .model_limits import opencode_context_limit, positive
        local = configured()
        config_provider = local.get(m["providerID"], {}) if m else {}
        models = config_provider.get('models')
        config_model = models.get(m['modelID'], {}) if m and isinstance(models, dict) else {}
        limits = config_model.get('limit') if isinstance(config_model, dict) else None
        explicit = positive(limits.get('context')) if isinstance(limits, dict) else None
        prov = {}
        if explicit is None:
            try:
                prov = await asyncio.wait_for(self._providers(), 4)
            except (OSError, HTTPError, ValueError, AttributeError, asyncio.TimeoutError):
                pass  # Metadata failure must not discard measured tokens or the fallback.
        native = next((p for p in prov.get("providers") or [] if isinstance(p, dict) and m and p.get("id") == m["providerID"]), {})
        limit = opencode_context_limit(m["providerID"], m["modelID"], local.get(m["providerID"]), native) if m else opencode_context_limit(None, None)
        if not self.models_cache:
            self._set_models(prov)
        mid = f"{m['providerID']}/{m['modelID']}" if m else selected
        upd = {"model": mid, "model_name": self.model_name(mid), "ctx": None}
        if type(used) is int and used >= 0:
            upd["ctx"] = {"used": used, "max": limit}
        if mid and include_quota:
            upd['quota_windows'] = await asyncio.to_thread(probe, mid.split('/', 1)[0])
        if self.cur_model() == selected:
            self.meter(**upd)

    def refresh_usage(self) -> asyncio.Task:
        """Independent, bounded context and subscription probes after a completed turn."""
        selected = self.cur_model()
        async def context():
            try:
                await asyncio.wait_for(self.context_meter(include_quota=False), 8)
            except (OSError, HTTPError, ValueError, AttributeError, asyncio.TimeoutError) as e:
                self.host.st.log('agent_meter_fail', agent=self.kind, reason=type(e).__name__)
        async def quota():
            from .provider_runtime import probe
            try:
                mid = selected or await self.quota_model()
                if not mid or '/' not in mid:
                    return
                windows = await asyncio.wait_for(asyncio.to_thread(probe, mid.split('/', 1)[0]), 10)
            except (OSError, ValueError, asyncio.TimeoutError):
                windows = []
            if self.cur_model() == selected:
                self.meter(quota_windows=windows)
        context_task = self._bg(context())
        self._bg(quota())
        return context_task

    def _set_models(self, prov: dict) -> None:
        from .provider_runtime import configured
        out, seen = [], set()
        local = [{"id": pid, **p} for pid, p in configured().items() if isinstance(p, dict)]
        for p in local + (prov.get("providers") or []):
            if not isinstance(p, dict) or not isinstance(p.get("id"), str) or not isinstance(p.get("models"), dict):
                continue
            for mid, mm in p["models"].items():
                full = f"{p['id']}/{mid}"
                if isinstance(mid, str) and slash.MODEL_RE.match(full):
                    name = mm.get("name") if isinstance(mm, dict) and isinstance(mm.get("name"), str) else mid
                    if full in seen:
                        continue
                    seen.add(full)
                    out.append({"id": clean_line(full, 100), "name": clean_line(name, 60), "efforts": None})
                    if len(out) >= 40:
                        break
            if len(out) >= 40:
                break
        conn = prov.get("connected")
        anonymous = prov.get("anonymous_models") or []
        if isinstance(conn, list):            # P423 #146: credential connections plus explicit anonymous routes
            self.usable_models = [{"id": clean_line(f"{p['id']}/{mid}", 100), "name": clean_line(
                mm.get("name") if isinstance(mm, dict) and isinstance(mm.get("name"), str) else mid, 60),
                "desc": clean_line(p.get("name") or "", 60)}
                for p in prov.get("providers") or [] if isinstance(p, dict) and isinstance(p.get("id"), str)
                and isinstance(p.get("models"), dict) for mid, mm in p["models"].items()
                if p.get("id") in conn or f"{p['id']}/{mid}" in anonymous
                if isinstance(mid, str) and slash.MODEL_RE.match(f"{p['id']}/{mid}")][:40]
        if out:
            self.models_cache = out[:40]
            fn = getattr(self.host, "models_changed", None)
            if fn:
                fn()

    async def refresh_busy(self):
        """Poll child session state, not the long-lived server PID; unavailable state expires by activity TTL."""
        b = getattr(self.host, "busy", None)
        if b is None:
            return
        scope, client, proc = self.busy_scope(), self.client, self.proc
        rows = [(k, v) for k, v in list(b.mem.items()) if v.get("session") == scope and v.get("native_child")]
        states = None
        if client and rows:
            try:
                st, data = await client.request("GET", "/api/session/active" if self.v2 else "/session/status", timeout=2)
                if self.v2:
                    from .agent_opencode2 import _data
                    data = _data(data)
                if st == 200 and isinstance(data, dict):
                    states = data
            except (OSError, HTTPError, asyncio.TimeoutError):
                pass
        for key, value in rows:
            cid = value.get("native_id") or next((a.split(":s:", 1)[1] for a, target in b.alias.items() if target == key and a.startswith(scope+":s:")), None)
            status = "unknown"
            if cid and states is not None and not self.v2:
                state = states.get(cid, {"type":"idle"})
                kind = state.get("type") if isinstance(state, dict) else None
                status = "running" if kind in ("busy", "retry") else "done" if kind == "idle" else "unknown"
            elif cid and client and self.v2:
                state = states.get(cid) if states is not None else None
                if isinstance(state, dict) and state.get("type") in ("busy", "retry"):
                    if client is self.client and proc is self.proc and b.mem.get(key) is value:
                        b.native_status(key, "running")
                    continue
                from urllib.parse import quote
                try:
                    st, data = await client.request("GET", f"/api/session/{quote(cid, safe='')}", timeout=2)
                    from .agent_opencode2 import _data
                    data = _data(data)
                    clock = data.get("time") if st == 200 and isinstance(data, dict) else None
                    # A previous execution's idle marker cannot negate this execution's start.
                    # Preserve its observation without refreshing TTL; only the active API renews it.
                    if isinstance(clock, dict) and type(clock.get("idle")) in (int, float):
                        started = value.get("native_started_at", value["since"]) * 1000
                        status = "done" if clock["idle"] > started else None
                except (OSError, HTTPError, asyncio.TimeoutError):
                    pass
            if status is not None and client is self.client and proc is self.proc and b.mem.get(key) is value:
                b.native_status(key, status)

    async def refresh_models(self) -> None:
        self._set_models(await self._providers() if self.client else {})

    async def apply_model(self, model, effort, default: bool = False):
        """OpenCode: the model rides on every prompt_async ("provider/model"); no effort until a probe proves the field."""
        if model is not None and not split_model(model):
            return "unknown_model"
        previous = self.cur_model()
        why = await super().apply_model(model, effort, default)
        if why is None and self.cur_model() != previous:
            self.meter(ctx=None, quota_windows=[])
        return why

    async def _wait_turn(self) -> None:
        """Until session.idle; every WATCH s also ask OpenCode itself, so a lost idle event cannot hang the queue."""
        quiet_since = None
        while not self.turn_done.is_set():
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.turn_done.wait(), WATCH)
            if not self.pending and not self.questions and time.monotonic() - self.turn_progress >= TURN_IDLE:
                self.fail_notice("OpenCode 长时间没有回复。这条消息不会自动重发；可在手机「模型和 API Key」检查或切换服务商后重试。" if self.cfg.get('language', 'en').startswith('zh') else "OpenCode has not responded for a long time. This message will not be resent automatically. Check or switch providers in Models & API keys on the phone, then retry.")
                await self.interrupt_request(self.proc)
                self._turn_end()
                return
            if self.pending or self.questions:
                self.turn_progress = time.monotonic()
            if self.turn_done.is_set() or not self.client or self.pending:
                quiet_since = None
                continue
            try:
                st, busy = await self.client.request("GET", "/session/status")
            except (OSError, HTTPError, asyncio.TimeoutError):
                continue
            s = busy.get(self.sid) if st == 200 and isinstance(busy, dict) else {"type": "?"}
            if isinstance(s, dict) and s.get("type") in ("busy", "retry"):
                self.saw_busy, quiet_since = True, None
            elif quiet_since is None:
                quiet_since = time.monotonic()            # idle twice in a row (≥ one WATCH apart) = done
            elif time.monotonic() - quiet_since >= WATCH:
                self._turn_end()

    async def stop(self) -> None:
        await super().stop()
        p = self.proc
        if p:
            await self._kill(p)
        self.proc = None
        for t in list(self.tasks):
            t.cancel()

    async def interrupt_request(self, p) -> None:
        """OpenCode's own `POST /session/{id}/abort` (ends the running prompt and its tool), then ≤ 2 s for the idle event;
        halt() ends the process tree right after (ADR-A72)."""
        td = self.turn_done
        if td is None or td.is_set() or not self.client or not self.sid:
            return
        with contextlib.suppress(OSError, HTTPError, asyncio.TimeoutError):
            await self.client.request("POST", f"/session/{self.sid}/abort", None, timeout=3)
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(td.wait(), 2)

    # ------------------------------------------------ a scheduled run (tasks.py): its own session
    async def run_once(self, prompt: str) -> tuple[str, int | str]:
        self.collect = []
        try:
            await self.turn(prompt)
            return "\n\n".join(self.collect), 0 if self.sid else "no_start"
        finally:
            if self.proc:
                await self._kill(self.proc)
            for t in list(self.tasks):
                t.cancel()

    # ------------------------------------------------ slash commands (slash.py)
    async def quota_model(self) -> str | None:
        selected = self.cur_model()
        if selected:
            return selected
        m = self._model_of(await self._last_assistant())
        if m:
            self._quota_model = f"{m['providerID']}/{m['modelID']}"
        if getattr(self, "_quota_model", None):
            return self._quota_model
        try:
            status, cfg = await self.client.request("GET", "/config")
            mid = cfg.get("model") if status == 200 and isinstance(cfg, dict) else None
            return mid if split_model(mid) else None
        except (OSError, HTTPError, AttributeError, asyncio.TimeoutError):
            return None

    async def drop_conversation(self) -> None:
        self._quota_model = await self.quota_model()
        self.sid = None

    async def cmd_clear(self, arg: str) -> Result:
        res = await super().cmd_clear(arg)
        if res.undo:
            self.refresh_usage()
        return res

    async def _ready(self) -> bool:
        if not await self._ensure_started():
            return False
        ok = await self._session_ok()
        if ok:
            await self.refresh_models()
            self.meter(model=self.cur_model(), model_name=self.model_name(self.cur_model()))
        return ok

    async def _providers(self) -> dict:
        try:
            st, r = await self.client.request("GET", "/provider")
        except (OSError, HTTPError, AttributeError, asyncio.TimeoutError):
            return {}
        return {"providers": r.get("all", []), "default": r.get("default", {}),
                "connected": r.get("connected")} if st == 200 and isinstance(r, dict) else {}

    async def authentication(self) -> dict:
        prov = await self._providers()
        connected = prov.get('connected')
        selected = split_model(self.cfg.get('model'))
        pid = selected['providerID'] if selected else None
        known = isinstance(connected, list) and all(isinstance(x, str) for x in connected)
        anonymous = prov.get('anonymous_models') or []
        return {'known': known, 'selected_connected': ((pid in connected or self.cfg.get('model') in anonymous)
                                                      if pid and known else None),
                'connected_count': len(connected) if known else None,
                'restart_after_key_change': True, 'subagents': self.subagent_state(),
                **({'running_version': self._running_version} if getattr(self, 'proc', None) is not None
                   and isinstance(getattr(self, '_running_version', None), str) else {})}

    async def _last_assistant(self) -> dict:
        """info of the newest assistant message of this conversation (tokens, modelID, providerID), or {}."""
        if not self.sid:
            return {}  # /clear may schedule a meter refresh before a new conversation exists.
        try:
            st, msgs = await self.client.request("GET", f"/session/{self.sid}/message")
        except (OSError, HTTPError, AttributeError, asyncio.TimeoutError):
            return {}
        for m in reversed(msgs if st == 200 and isinstance(msgs, list) else []):
            info = m.get("info") if isinstance(m, dict) else None
            if isinstance(info, dict) and info.get("role") == "assistant":
                return info
        return {}

    @staticmethod
    def _ctx(info: dict):
        t = info.get("tokens") if isinstance(info.get("tokens"), dict) else {}
        if isinstance(t.get("total"), int):
            return t["total"]
        c = t.get("cache") if isinstance(t.get("cache"), dict) else {}
        parts = [t.get("input"), t.get("output"), t.get("reasoning"), c.get("read"), c.get("write")]
        return sum(x for x in parts if isinstance(x, int)) if any(isinstance(x, int) for x in parts) else None

    def _model_of(self, info: dict) -> dict | None:
        m = split_model(self.cfg.get("model"))
        if m:
            return m
        if isinstance(info.get("providerID"), str) and isinstance(info.get("modelID"), str):
            return {"providerID": info["providerID"], "modelID": info["modelID"]}
        return None

    async def cmd_compact(self, arg: str) -> Result:
        if not self.host.st.agent_session(self.kind):
            return Result("还没有对话，不用压缩。", "info")
        if not await self._ready():
            return Result("OpenCode 没起来，没有压缩。", "error")
        info = await self._last_assistant()
        model = self._model_of(info)
        if model is None:
            prov = await self._providers()
            d = prov.get("default") if isinstance(prov.get("default"), dict) else {}
            if d:
                p0, m0 = next(iter(d.items()))
                model = {"providerID": p0, "modelID": m0}
        if model is None:
            return Result("没有压缩：不知道用哪个模型（先发一条消息，或用 /model 选一个）。", "error")
        before = self._ctx(info)
        self.quiet = True
        self.set_status("compacting")
        t0 = time.monotonic()
        try:
            st, _ = await self.client.request("POST", f"/session/{self.sid}/summarize", model, timeout=600)
            with contextlib.suppress(OSError, HTTPError, asyncio.TimeoutError):   # the summary's text parts are not replies
                st2, msgs = await self.client.request("GET", f"/session/{self.sid}/message")
                for m in msgs if st2 == 200 and isinstance(msgs, list) else []:
                    for part in (m.get("parts") if isinstance(m, dict) else None) or []:
                        if isinstance(part, dict) and isinstance(part.get("id"), str):
                            self.emitted.add(part["id"])
        except (OSError, HTTPError, asyncio.TimeoutError) as e:
            return Result(f"没有压缩：{type(e).__name__}", "error")
        finally:
            await asyncio.sleep(0.2)
            self.quiet = False
            self.set_status("working")
        if st != 200:
            return Result(f"没有压缩（HTTP {st}）。", "error")
        after = self._ctx(await self._last_assistant())
        took = slash.secs((time.monotonic() - t0) * 1000)
        if isinstance(before, int) and isinstance(after, int):
            return Result(f"已压缩：{slash.tokens(before)} → {slash.tokens(after)} tokens{took}")
        return Result(f"已压缩{took}。")

    async def cmd_context(self, arg: str) -> Result:
        if not self.host.st.agent_session(self.kind):
            return Result("上下文：还没有对话。", "info")
        if not await self._ready():
            return Result("OpenCode 没起来：" + slash.NONE, "error")
        info = await self._last_assistant()
        used = self._ctx(info)
        m = self._model_of(info)
        limit = None
        if m:
            for p in (await self._providers()).get("providers") or []:
                if isinstance(p, dict) and p.get("id") == m["providerID"]:
                    mm = (p.get("models") or {}).get(m["modelID"]) if isinstance(p.get("models"), dict) else None
                    lim = mm.get("limit") if isinstance(mm, dict) else None
                    limit = lim.get("context") if isinstance(lim, dict) else None
        if used is None:
            return Result("上下文：" + slash.NONE + "（这段对话还没有回复）", "info")
        return Result(f"上下文 {slash.tokens(used)} / {slash.tokens(limit)}{slash.pct(used, limit)}"
                      + (f" · 模型 {m['providerID']}/{m['modelID']}" if m else ""))

    async def cmd_cost(self, arg: str) -> Result:
        if not self.host.st.agent_session(self.kind):
            return Result("本会话花费：$0（还没有对话）", "info")
        if not await self._ready():
            return Result("OpenCode 没起来：" + slash.NONE, "error")
        try:
            st, s = await self.client.request("GET", f"/session/{self.sid}")
        except (OSError, HTTPError, asyncio.TimeoutError):
            st, s = 0, None
        if st != 200 or not isinstance(s, dict):
            return Result("本会话花费：" + slash.NONE, "error")
        t = s.get("tokens") if isinstance(s.get("tokens"), dict) else {}
        c = t.get("cache") if isinstance(t.get("cache"), dict) else {}
        return Result(f"本会话花费（OpenCode 报告）：{slash.money(s.get('cost'))}\ntoken：输入 {slash.tokens(t.get('input'))}"
                      f"（缓存读 {slash.tokens(c.get('read'))}）· 输出 {slash.tokens(t.get('output'))}"
                      f"（含推理 {slash.tokens(t.get('reasoning'))}）" + await self._children_line(s.get("cost")))

    async def _children(self) -> list:
        """P426: this conversation's sub-agent sessions with their cost / tokens (v1: summed from their messages)."""
        st, kids = await self.client.request("GET", f"/session/{self.sid}/children")
        out = []
        for k in kids[:50] if st == 200 and isinstance(kids, list) else []:
            if not isinstance(k, dict) or not isinstance(k.get("id"), str):
                continue
            st, msgs = await self.client.request("GET", f"/session/{k['id']}/message")
            u = {"cost": 0.0, "tokens": {"input": 0, "output": 0}}
            for m in msgs if st == 200 and isinstance(msgs, list) else []:
                i = m.get("info") if isinstance(m, dict) else None
                if isinstance(i, dict) and i.get("role") == "assistant":
                    u["cost"] += i["cost"] if type(i.get("cost")) in (int, float) else 0
                    for f in ("input", "output"):
                        x = (i.get("tokens") or {}).get(f) if isinstance(i.get("tokens"), dict) else None
                        u["tokens"][f] += x if type(x) is int else 0
            out.append(u)
        return out

    async def _children_line(self, main_cost) -> str:
        """P426: sub-agents' usage counted into the conversation's (/cost)."""
        try:
            kids = await self._children()
        except (OSError, HTTPError, ValueError, AttributeError, asyncio.TimeoutError):
            return ""
        if not kids:
            return ""
        num = lambda v: v if type(v) in (int, float) and v >= 0 else 0  # noqa: E731
        cost = sum(num(k.get("cost")) for k in kids)
        tok = [sum(num((k.get("tokens") or {}).get(f)) for k in kids if isinstance(k.get("tokens"), dict)) for f in ("input", "output")]
        total = f"\n合计（含子 Agent）：{slash.money(cost + main_cost)}" if type(main_cost) in (int, float) else ""
        return (f"\n子 Agent（{len(kids)} 个）：{slash.money(cost)} · token：输入 {slash.tokens(tok[0])} · 输出 {slash.tokens(tok[1])}"
                + total)

    async def cmd_usage(self, arg: str) -> Result:
        return Result("套餐用量：" + slash.NONE + "（OpenCode 不报告额度：在你注册的模型服务商那边看）", "info")

    async def cmd_status(self, arg: str) -> Result:
        from .opencode_subagents import state_text
        ver = ""
        if await self._ensure_started():
            with contextlib.suppress(OSError, HTTPError, AttributeError, asyncio.TimeoutError):
                st, h = await self.client.request("GET", "/global/health")
                if st == 200 and isinstance(h, dict) and isinstance(h.get("version"), str):
                    ver = clean_line(h["version"], 40)
        lines = [f"Agent：OpenCode {ver}".rstrip(), f"对话：{self.host.st.agent_session(self.kind) or '（新对话）'}",
                 f"模型：{self.cfg.get('model') or '默认'}", f"目录：{self.cfg['dir']}",
                 "审批：除只读工具外都问手机（Agent J 的会话规则）；危险清单生效",
                 state_text(self.subagent_state()),
                 "隔离（fence）：" + ("开" if self.cfg.get("fence", True) else "关（--unfenced）")]
        return Result("\n".join(lines))

    async def cmd_model(self, arg: str) -> Result:
        if not await self._ensure_started():
            return Result("OpenCode 没起来，没法换模型。", "error")
        prov = await self._providers()
        models = []
        for p in prov.get("providers") or []:
            if not isinstance(p, dict) or not isinstance(p.get("id"), str) or not isinstance(p.get("models"), dict):
                continue
            for mid, mm in p["models"].items():
                if isinstance(mid, str):
                    name = mm.get("name") if isinstance(mm, dict) and isinstance(mm.get("name"), str) else mid
                    full = f"{p['id']}/{mid}"
                    # P298: a free model whose provider keeps / trains on the conversation says so on the card
                    warn = " · ⚠ " + _free_terms(full) if _free_terms(full) else ""
                    models.append({"id": clean_line(full, 100), "name": clean_line(name, 60),
                                   "desc": clean_line(p.get("name") or "", 60) + warn})
        cur = self.cfg.get("model") or ""
        if not arg:
            for m in models:
                m["cur"] = m["id"] == cur
            return Result(f"当前模型：{cur or '默认'}。" + ("点一个切换（之后的对话都用它）：" if models else "OpenCode 没有给出可选列表。"),
                          models=models[:40])
        if not split_model(arg) or not slash.MODEL_RE.match(arg) or (models and arg not in {m["id"] for m in models}):
            return Result(f"没有这个模型：{clean_line(arg, 100)}（写成「服务商/模型」）", "error")
        self.host.set_model(arg)
        self.meter(model=arg, model_name=self.model_name(arg))
        from .opencode_free import privacy_note
        note = privacy_note(arg, "zh")
        return Result(humane.of(self, "model.switched_next", model=arg) + ("\n" + note if note else ""))
