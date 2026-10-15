"""P279 (Leo, 2026-10-14): which AI route to recommend at install / first run — `agentj agent route [--json]`.

Decided by what actually works from this computer, not by guessing: one unauthenticated GET each to api.anthropic.com
(Claude Code), api.openai.com (Codex, chatgpt_reach) and api.kimi.com (the Kimi route itself). No key is sent; only the
status code and the provider's error type are read — the same answer the provider gives to every unsupported region
(Anthropic 403 "Request not allowed", OpenAI 403 unsupported_country). Optional local signals (time zone, locale) only
break a tie; they are read on this computer and never sent anywhere.

  any official service answers        → "official"  (the install.md harness rule as before: Claude Code / Codex)
  both refused or unreachable         → "kimi"      (Kimi membership Plus or higher in OpenCode — or, P305, in Kimi's own
                                                      CLI `kimi` (KIMI_CLI); + alternatives)
  … and Kimi unreachable as well      → "offline"   (no network: recommend nothing, check the network first)
  one refused by REGION, other unknown → "kimi"     (partial)
  nothing measured, zone HK / MO / CN → "kimi"      (hint only — the text says so)
  otherwise                           → "unsure"    (official rule; Kimi named as the fallback)

The invite link lives in ONE place: ai_route.json next to this file; a bound host prefers the platform's partner entry
(slot ai-coding, name Kimi — changeable without a release). Every link is printed with its disclosure right next to it.
The key goes only through the phone key card into the service environment; this module never reads any key.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

DATA = pathlib.Path(__file__).with_name("ai_route.json")
CACHE = "ai_route.json"                      # <state>/setup/ai_route.json: codes + the resolved link, never a key
SLOT = "ai-coding"
TIMEOUT = 5.0
ROUTES = ("official", "kimi", "offline", "unsure")
RESTRICTED = ("CN", "HK", "MO")               # Claude Code and Codex are not offered there (Anthropic / OpenAI region lists)
ZONES = {"Asia/Hong_Kong": "HK", "Hongkong": "HK", "Asia/Macau": "MO", "Asia/Macao": "MO", "Asia/Shanghai": "CN",
         "Asia/Chongqing": "CN", "Asia/Chungking": "CN", "Asia/Harbin": "CN", "Asia/Urumqi": "CN", "Asia/Kashgar": "CN",
         "PRC": "CN"}
LOCALES = {"zh_HK": "HK", "en_HK": "HK", "zh_MO": "MO", "zh_CN": "CN"}
REGION_WORDS = ("request not allowed", "unsupported_country", "country, region, or territory", "not available in your",
                "region is not supported", "location is not supported")
PROBES = {"anthropic": "https://api.anthropic.com/v1/models", "kimi": None}   # openai: chatgpt_reach.URL

DISCLOSURE = {"zh": "这是 Agent J 官方的邀请链接，您和我们各得一次抽奖",
              "en": "This is Agent J's official invite link: you and we each get one prize draw"}


# ------------------------------------------------------------------ the one configurable place
def data() -> dict:
    return json.loads(DATA.read_text(encoding="utf-8"))["kimi"]


def _on_hosts(url, hosts) -> str | None:
    if not isinstance(url, str) or len(url) > 300 or any(c.isspace() for c in url) or not url.isprintable():
        return None
    try:
        u = urllib.parse.urlsplit(url)
        u.port  # noqa: B018 — raises on a bad port
    except ValueError:
        return None
    if u.scheme != "https" or u.username or u.password or u.port or (u.hostname or "") not in hosts:
        return None
    return url


def _platform(st, call=None) -> dict | None:
    """The platform's Kimi entry in slot ai-coding (bound hosts only), or None. Never raises."""
    try:
        from . import cloud, market
        if st is None or not cloud.read_cloud(st):
            return None
        obj = call(st) if call else market._call(st, "partners", {"slot": SLOT})
        for p in market.parse_partners(obj.get("partners")):
            if p["slot"] == SLOT and p["name"].lower() == "kimi":
                return p
    except Exception:   # noqa: BLE001 — the platform is optional here; the bundled default always works
        return None
    return None


def invite(st=None, call=None, use_platform: bool = True) -> dict:
    """{link, official, invite: bool, source}. `invite` = the link is a referral → print DISCLOSURE next to it."""
    d = data()
    hosts = tuple(d["hosts"])
    p = _platform(st, call) if use_platform else None
    if p:
        link = _on_hosts(p.get("link"), hosts)
        if link:
            return {"link": link, "official": _on_hosts(p.get("url"), hosts) or d["official_url"],
                    "invite": bool(p.get("referral")), "source": "platform"}
    link = _on_hosts(d.get("invite_url"), hosts)
    return {"link": link or d["official_url"], "official": d["official_url"], "invite": bool(link), "source": "bundled"}


# ------------------------------------------------------------------ signals
def _get(url: str, environ: dict | None, timeout: float) -> str:
    """ok (the service answered: 2xx / 401 / 403-auth / 404 / 429) · region · unreachable · unknown."""
    from .chatgpt_reach import _opener
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": "agentj-route-probe"})
    try:
        with _opener(environ).open(req, timeout=timeout) as r:
            return "ok" if r.status < 500 else "unknown"
    except urllib.error.HTTPError as e:
        try:
            body = e.read(4000).decode("utf-8", "replace").lower()
        except Exception:  # noqa: BLE001
            body = ""
        finally:
            e.close()
        if e.code == 451 or (e.code == 403 and any(w in body for w in REGION_WORDS)):
            return "region"
        if e.code in (401, 403, 404, 429):
            return "ok"
        return "unknown"
    except (urllib.error.URLError, OSError, ValueError):
        return "unreachable"


def reach(environ: dict | None = None, timeout: float = TIMEOUT) -> dict:
    """{anthropic, openai, kimi}: each ok / region / unreachable / unknown, or skipped (tests: AGENTJ_REACH_PROBE=off)."""
    if os.environ.get("AGENTJ_REACH_PROBE") == "off":
        return {"anthropic": "skipped", "openai": "skipped", "kimi": "skipped"}
    from . import chatgpt_reach
    jobs = {"anthropic": lambda: _get(PROBES["anthropic"], environ, timeout),
            "openai": lambda: chatgpt_reach.probe(environ, timeout),
            "kimi": lambda: _get(data()["verify_url"], environ, timeout)}
    with ThreadPoolExecutor(max_workers=3) as ex:
        fut = {k: ex.submit(f) for k, f in jobs.items()}
        return {k: f.result() for k, f in fut.items()}


def _zone(environ: dict) -> str | None:
    tz = (environ.get("TZ") or "").lstrip(":")
    if tz:
        return tz
    try:
        target = os.readlink("/etc/localtime")
        m = re.search(r"zoneinfo/(.+)$", target)
        if m:
            return m.group(1)
    except OSError:
        pass
    try:
        return pathlib.Path("/etc/timezone").read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _locale(environ: dict) -> str | None:
    for k in ("LC_ALL", "LC_MESSAGES", "LANG"):
        v = environ.get(k)
        if v and v not in ("C", "POSIX") and not v.startswith("C."):
            return v.split(".")[0]
    return None


def hint(environ: dict | None = None, zone=None, locale=None) -> dict:
    """Local only: {zone, locale, region} — from the time zone; the locale only when no zone can be read."""
    e = os.environ if environ is None else environ
    z = zone if zone is not None else _zone(e)
    loc = locale if locale is not None else _locale(e)
    region = ZONES.get(z) if z else LOCALES.get(loc or "")     # a known zone outweighs a language setting
    return {"zone": z, "locale": loc, "region": region}


# ------------------------------------------------------------------ the decision (pure; the test matrix)
def decide(anthropic: str, openai: str, kimi: str = "unknown", region: str | None = None) -> dict:
    official = [n for n, r in (("claude", anthropic), ("codex", openai)) if r == "ok"]
    blocked = [r for r in (anthropic, openai) if r in ("region", "unreachable")]
    restricted = region in RESTRICTED
    if official:
        return {"route": "official", "confidence": "measured", "available": official}
    if len(blocked) == 2:
        if blocked == ["unreachable", "unreachable"] and kimi == "unreachable":
            return {"route": "offline", "confidence": "measured", "available": []}
        return {"route": "kimi", "confidence": "measured", "available": []}
    if len(blocked) == 1 and ("region" in blocked or restricted):
        return {"route": "kimi", "confidence": "partial", "available": []}
    if not blocked and restricted:
        return {"route": "kimi", "confidence": "hint", "available": []}
    return {"route": "unsure", "confidence": "partial" if blocked else "none", "available": []}


# ------------------------------------------------------------------ text
def alternatives(region: str | None) -> list[dict]:
    """Existing routes (install.md Step 3a, plaza packages), mainland-first when the zone is mainland China."""
    cn = [{"zh": "千问 Qwen 按量付费（阿里云百炼，中国大陆站，需实名；定时任务 / bot 用这条）",
           "en": "Qwen pay as you go (Alibaba Cloud Bailian, mainland China site; for scheduled tasks / bots)",
           "how": "agentj plaza show qwen-in-opencode-cn", "url": "https://bailian.console.aliyun.com/"},
          {"zh": "火山方舟 Coding Plan（字节豆包 / Kimi / GLM / DeepSeek 一个套餐；已经订了方舟的用这条）",
           "en": "Volcano Ark Coding Plan (Doubao / Kimi / GLM / DeepSeek in one plan; for owners already on Ark)",
           "how": "agentj provider ark", "url": "https://console.volcengine.com/ark/"},   # P315: ark.py
          {"zh": "智谱 GLM-5.3 / DeepSeek（install.md 第 3a 步）", "en": "Zhipu GLM-5.3 / DeepSeek (install.md Step 3a)",
           "how": "agentj agent switch opencode --model zhipuai/glm-5.3", "url": "https://open.bigmodel.cn/"}]
    intl = [{"zh": "OpenRouter（国际信用卡，按量付费）", "en": "OpenRouter (card, pay as you go)",
             "how": "agentj agent switch opencode --model openrouter/<model>", "env": "OPENROUTER_API_KEY", "url": "https://openrouter.ai/"},
            {"zh": "Kimi 开放平台按量付费（不是会员）", "en": "Kimi open platform, pay as you go (not the membership)",
             "how": "agentj agent switch opencode --model moonshotai/kimi-k3", "env": "MOONSHOT_API_KEY", "url": "https://platform.kimi.ai/"}]
    return cn + intl if region == "CN" else intl + cn


def steps(lang: str, env_file: str) -> list[str]:
    d = data()
    k, v = d["key_env"], d["verify_url"]
    card = f"agentj secret request --name {k} --purpose 'Kimi Code' --dest 'env:{env_file}#{k}' --verify-url {v}"   # Bearer = default
    if lang == "zh":
        return [f"主人打开上面的链接，订阅 **Plus 或更高档**（Go 档没有编程额度；邀请抽奖要求 24 小时内付费订阅）。",
                f"主人在 Kimi Code 控制台 {d['console_url']} 新建一把 key（只显示一次；不要发到聊天里）。",
                f"你运行：`{card}` —— 主人只把 key 粘贴进手机密钥卡。",
                "`agentj service restart`，然后 `agentj agent switch --scan` 确认 OpenCode 能用。",
                f"`agentj agent switch opencode --model {d['model']}`（轻任务可用 {d['model_light']}）。"]
    return [f"The owner opens the link above and subscribes to **Plus or higher** (Go has no coding quota; the invite draw "
            f"needs a paid subscription within 24 hours).",
            f"The owner creates a key in the Kimi Code console {d['console_url']} (shown once; never paste it into chat).",
            f"You run: `{card}` — the owner pastes the key only into the phone's key card.",
            "`agentj service restart`, then `agentj agent switch --scan` to confirm OpenCode is usable.",
            f"`agentj agent switch opencode --model {d['model']}` (light tasks: {d['model_light']})."]


# P310: Alibaba Cloud Bailian Token Plan (personal) — the second mainland recommendation, side by side with Kimi (bailian.py)
def bailian_info() -> dict:
    from . import bailian
    p = bailian.plan("token-plan")
    return {"plan": "token-plan", "provider": p["provider"], "model": f"{p['provider']}/{p['model']}",
            "model_light": f"{p['provider']}/{p['model_light']}", "key_env": p["key_env"], "buy_url": p["buy_url"],
            "tiers": [{"name": n, "cny_month": c, "credits": cr} for n, c, cr in p["tiers"]],
            "harness_options": ["opencode", "qwen"], "terms": bailian.TERMS}


def bailian_steps(lang: str) -> list[str]:
    from . import bailian
    b = bailian_info()
    if lang == "zh":
        return [f"价格（个人版，按 Credits 计量）：{bailian.tiers_text('token-plan', 'zh')}。Standard 起可同时跑 3–4 个 Agent。",
                f"主人在 {b['buy_url']} 订阅 Token Plan 个人版，在控制台「套餐专属 API Key」新建 key（不要发到聊天里）。",
                "你运行 `agentj provider bailian`：把百炼写进 OpenCode 配置（已有的配置不动），并打印密钥卡命令；"
                f"主人只把 key 粘贴进手机密钥卡（{b['key_env']}）。",
                f"`agentj service restart`，然后 `agentj agent switch opencode --model {b['model']}`"
                f"（能看图，截图、浏览器自动化都用得上；省额度用 {b['model_light']}）。"
                "也可以用阿里自己的 Qwen Code：`agentj agent switch qwen`（同一把 key）。",
                "⚠ " + bailian.TERMS["zh"]]
    return [f"Prices (personal, metered in Credits): {bailian.tiers_text('token-plan', 'en')}. Standard and up run 3–4 Agents at once.",
            f"The owner subscribes to Token Plan (personal) at {b['buy_url']} and creates the plan's own API key in the console "
            "(never paste it into chat).",
            "You run `agentj provider bailian`: it adds Bailian to OpenCode's config (existing settings untouched) and prints the "
            f"key-card command; the owner pastes the key only into the phone's key card ({b['key_env']}).",
            f"`agentj service restart`, then `agentj agent switch opencode --model {b['model']}` (reads images — screenshots and "
            f"browser work; lighter: {b['model_light']}). Alibaba's own Qwen Code works too: `agentj agent switch qwen` (same key).",
            "⚠ " + bailian.TERMS["en"]]


# P305: the same membership in Kimi's own CLI instead of OpenCode — the owner picks (harness_options in --json)
HARNESS_OPTIONS = ("opencode", "kimi")


def kimi_cli_steps(lang: str) -> list[str]:
    if lang == "zh":
        return ["装 Kimi Code CLI（官方，2.2.0 或更新）：`curl -fsSL https://code.kimi.com/kimi-code/install.sh | bash`"
                "（不用 Node.js；也可以 `npm install -g @moonshot-ai/kimi-code`）。",
                "登录（二选一）：手机 设置 →「AI 登录救援」→ Kimi Code 设备码登录；或主人在电脑终端运行 `kimi login --region mainland-cn`"
                "（海外站 kimi.ai 的会员用 `--region global`）。也可以用上面第 3 步的手机密钥卡存 KIMI_API_KEY。",
                "`agentj agent switch kimi`（kimi.ai 的会员加 `--region global`）：先无头测试，能用才切，失败自动切回。"]
    return ["Install Kimi Code CLI (official, 2.2.0 or newer): `curl -fsSL https://code.kimi.com/kimi-code/install.sh | bash` "
            "(no Node.js needed; or `npm install -g @moonshot-ai/kimi-code`).",
            "Sign in (either): phone Settings → AI login rescue → Kimi Code device code; or the owner runs `kimi login --region mainland-cn` "
            "in a terminal (`--region global` for a kimi.ai membership). The key card of step 3 (KIMI_API_KEY) works too.",
            "`agentj agent switch kimi` (add `--region global` for kimi.ai): tested headless first, switched only if it works, "
            "switched back on failure."]


def link_line(inv: dict, lang: str) -> str:
    """The link and its disclosure TOGETHER — never one without the other."""
    if not inv["invite"]:
        return inv["link"]
    return f"{inv['link']}（{DISCLOSURE['zh']}）" if lang == "zh" else f"{inv['link']} ({DISCLOSURE['en']})"


HEAD = {
    "official": ("Claude Code / Codex 在这台电脑上能连通：按 install.md 的规则选（只有一个能用就用它，多个就问主人）。",
                 "Claude Code / Codex can be reached from this computer: follow the install.md rule (one usable → that one; "
                 "several → ask the owner)."),
    "kimi": ("这台电脑所在地区用不了 Claude Code 和 Codex。推荐两条，并列、让主人选：**Kimi 会员 Plus 或更高档**，或 **阿里百炼 "
             "Token Plan 个人版**（见下）。Kimi 用 OpenCode 接入：",
             "Claude Code and Codex are not available where this computer is. Two recommendations, side by side — let the owner "
             "choose: **Kimi membership Plus or higher**, or **Alibaba Cloud Bailian Token Plan (personal)** (below). Kimi, "
             "through OpenCode:"),
    "offline": ("Claude Code、Codex、Kimi 都连不上：先检查这台电脑的网络，再运行一次 `agentj agent route`。",
                "Neither Claude Code, Codex nor Kimi can be reached: check this computer's network first, then run "
                "`agentj agent route` again."),
    "unsure": ("没能确认 Claude Code / Codex 在这里能不能用：先按 install.md 的规则试；第一条消息就失败（地区不支持）时，改用下面的 Kimi 路线。",
               "Could not confirm whether Claude Code / Codex work here: try them first (install.md rule); if the first "
               "message fails (region not supported), use the Kimi route below."),
}
HINT_NOTE = ("（只按本机时区判断，没有实测成功；装好后用 `agentj agent switch --scan` 确认。）",
             "(Judged from this computer's time zone only, not measured; confirm with `agentj agent switch --scan` later.)")
GLOBAL_NOTE = ("  注：上面是 kimi.com 的会员（实测通过）。在海外站 kimi.ai 买的会员，key 不通用：改用 {model_global}，验证地址 {verify_url_global}。",
               "  Note: the above is a kimi.com membership (measured). A membership bought on kimi.ai is a different site with "
               "different keys: use {model_global} and verify against {verify_url_global}.")
REASON = {"region": ("不服务此地区", "region not served"), "unreachable": ("连不上", "unreachable"), "ok": ("能连通", "reachable"),
          "unknown": ("没能确认", "not confirmed"), "skipped": ("未探测", "not probed")}


def render(res: dict, lang: str) -> str:
    i = 0 if lang == "zh" else 1
    s = res["signals"]
    out = [("AI 路线" if i == 0 else "AI route") + f": {res['route']}",
           " · ".join(f"{lab} {REASON.get(s[k], (s[k], s[k]))[i]}"
                      for k, lab in (("anthropic", "Claude Code"), ("openai", "Codex"), ("kimi", "Kimi"))),
           HEAD[res["route"]][i]]
    if res["confidence"] == "hint":
        out.append(HINT_NOTE[i])
    if res["route"] in ("kimi", "unsure"):
        out.append("→ " + link_line(res["link"], lang))
        out += [f"  {n}. {t}" for n, t in enumerate(steps(lang, res["env_file"]), 1)]
        out.append(GLOBAL_NOTE[i].format(**data()))
        out.append("或者不用 OpenCode，用 Kimi 自己的 Kimi Code CLI 接同一个会员（让主人选）：" if i == 0 else
                   "Or, instead of OpenCode, Kimi's own Kimi Code CLI on the same membership (let the owner choose):")
        out += [f"  {n}. {t}" for n, t in enumerate(kimi_cli_steps(lang), 1)]
        out.append("并列推荐：阿里百炼 Token Plan 个人版（大陆直连，OpenCode 或 Qwen Code）：" if i == 0 else
                   "Side by side: Alibaba Cloud Bailian Token Plan, personal (mainland China, OpenCode or Qwen Code):")
        out += [f"  {n}. {t}" for n, t in enumerate(bailian_steps(lang), 1)]
        out.append("备选：" if i == 0 else "Alternatives:")
        out += [f"  - {a[lang]} {a['url']} — `{a['how']}`" + (f"（{a['env']}）" if lang == "zh" and a.get("env") else
                f" ({a['env']})" if a.get("env") else "") for a in res["alternatives"]]
        out.append("已经有其中某个服务账号的，继续用已有账号。" if i == 0 else
                   "Already have an account with one of these? Keep using it.")
    return "\n".join(out)


# ------------------------------------------------------------------ the whole thing
def _env_file() -> str:
    from . import service
    home = os.path.expanduser("~")
    p = service.env_file(service.name())
    return "~" + p[len(home):] if p.startswith(home + os.sep) else p


def run(st=None, environ: dict | None = None, signals: dict | None = None, call=None, use_platform: bool = True) -> dict:
    s = signals or reach(environ)
    h = hint(environ)
    res = decide(s["anthropic"], s["openai"], s.get("kimi", "unknown"), h["region"])
    res.update(signals=s, hint=h, checked_at=int(time.time()), env_file=_env_file(),
               link=invite(st, call, use_platform and res["route"] in ("kimi", "unsure")),
               model=data()["model"], key_env=data()["key_env"], alternatives=alternatives(h["region"]),
               harness_options=list(HARNESS_OPTIONS) if res["route"] in ("kimi", "unsure") else [],
               bailian=bailian_info() if res["route"] in ("kimi", "unsure") else None)
    _save(st, res)
    return res


def _save(st, res: dict) -> None:
    """Remember the answer for the first-run card (no network there). Codes and the link only; never a key."""
    if st is None or not st.exists():
        return
    try:
        from . import first_run
        keep = {k: res[k] for k in ("route", "confidence", "checked_at")}
        keep.update(region=res["hint"]["region"], link=res["link"])
        st.write_private(first_run._dir(st) / CACHE, json.dumps(keep, ensure_ascii=False, sort_keys=True).encode())
    except Exception:   # noqa: BLE001 — a cache, never a failure
        pass


def cached(st, max_age: int = 7 * 24 * 3600) -> dict | None:
    try:
        rec = json.loads((st.root / "setup" / CACHE).read_text(encoding="utf-8"))
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(rec, dict) or rec.get("route") not in ROUTES or time.time() - int(rec.get("checked_at") or 0) > max_age:
        return None
    link = rec.get("link") if isinstance(rec.get("link"), dict) else {}
    ok = _on_hosts(link.get("link"), tuple(data()["hosts"]))
    rec["link"] = {"link": ok, "official": data()["official_url"], "invite": bool(link.get("invite")), "source": "cache"} \
        if ok else invite(use_platform=False)
    return rec


def journal_entry(v) -> dict | None:
    """The checklist journal's copy ({route, link}), re-validated on every read: a link off the Kimi hosts is dropped."""
    if not isinstance(v, dict) or v.get("route") != "kimi" or not isinstance(v.get("link"), dict):
        return None
    link = _on_hosts(v["link"].get("link"), tuple(data()["hosts"]))
    return {"route": "kimi", "link": {"link": link, "official": data()["official_url"], "invite": v["link"].get("invite") is True,
                                       "source": "journal"}} if link else None


def main(st=None, as_json: bool = False, lang: str = "zh", out=None) -> int:
    out = out or sys.stdout
    res = run(st)
    if as_json:
        print(json.dumps({**res, "disclosure": DISCLOSURE if res["link"]["invite"] else None}, ensure_ascii=False), file=out)
    else:
        print(render(res, lang), file=out)
    return 0
