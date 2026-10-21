"""Plaza permission declarations (P426 #181-1): what a skill / workflow says it needs, compared with what this computer's Agent
is already allowed to do. A declared set that is not a subset of the allowed set is a permission escalation (Agentic Skills
survey, arxiv 2608.29596): the install stops and the owner decides on the phone, with a signed approval card.

Manifest (`permissions`, optional; CATALOG_FORMAT addition, checked by bundle.py / dashboard bundle.ts):
    {"tools": ["Bash", "Write", "WebFetch", …],   ≤ 40 tool names ^[A-Za-z][A-Za-z0-9_]{0,63}$ (the harness's tool names)
     "network": bool,                             it reaches the internet
     "exec": bool,                                it runs programs / scripts (its own scripts/, bin/ … included)
     "secrets": ["OPENAI_API_KEY", …]}            ≤ 60 environment variable names it reads as secrets
Unknown keys are refused. A package without `permissions` gets one inferred from its content (infer()): exec when it carries
scripts/ cli/ tools/ bin/ or script files or an install.verify; network when its code talks to the internet or its docs use
curl / wget / WebFetch; tools from SKILL.md `allowed-tools:` and role files' `tools:` (+ Bash with exec); secrets from
requires.env entries marked secret. `agentj plaza publish` shows the inferred set and publishes it inside the manifest (the
owner's yes to the preview is the author's confirmation); `install` of an old package without a declaration uses the inference.
A declaration never lowers what the content shows (security review S1): install compares declared ∪ inferred (effective());
when the declaration leaves out something the content shows, the preview and the card say 「包声明的少于实际内容」 and the left-out
items are asked like any other — writing `"permissions": {}` does not skip the card.

Allowed set (allowed()), the authoritative source per harness, read-only:
  claude_code  ~/.claude/settings.json + settings.local.json: permissions.defaultMode "bypassPermissions" → everything;
               else the bare tool names in permissions.allow (a rule with arguments, e.g. "Bash(git:*)", allows only part of
               the tool, so it does not count); exec = Bash allowed; network = WebFetch allowed (or Bash).
  codex        ~/.codex/config.toml: sandbox_mode "danger-full-access" with approval_policy "never" → everything;
               "workspace-write" → its tools + exec, network only with sandbox_workspace_write.network_access = true.
  opencode     no readable source → nothing counts as allowed (every declared item asks).
Read-only tools Claude Code itself never asks for (Read, Glob, Grep, LS, NotebookRead, TodoWrite) are always allowed. Secrets
are never "already allowed" except under full access: the first install of a package that reads a secret asks.
What the owner approved is recorded in installed.json (`permissions`, `perm_grant`); a later version asks again only for
what it adds.

The card: `agentj plaza install --owner-confirmed` asks serve over the control socket (`{"cmd": "plaza_perm"}`); serve raises a
normal approval card (tool "agentj plaza" — a host card, never auto-allowed by any approval level, and in goal.SOFT_EXEMPT so a
running goal never auto-approves it either) listing every extra item in plain words, and only an allow signed by a paired
device counts. The same card carries a publisher change (market_guard TOFU, security review N4): the old and the new
publisher fingerprint (or 「未签名」) are listed and the owner's signed allow is the only way to accept a new publisher. Without serve (a plain CLI, cron) the install is refused
and says how to approve it from the phone. Standard library only.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import tomllib

KEYS = ("tools", "network", "exec", "secrets")
TOOL = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}")
ENV = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
ALWAYS = frozenset(("Read", "Glob", "Grep", "LS", "NotebookRead", "TodoWrite"))
EXEC_DIRS = frozenset(("scripts", "cli", "tools", "bin"))
SCRIPT_EXT = (".sh", ".bash", ".zsh", ".py", ".js", ".mjs", ".cjs", ".ts", ".rb", ".pl", ".ps1", ".php", ".lua")
_DOC_EXT = (".md", ".txt", ".rst")
_NET_CODE = re.compile(r"https?://|\b(?:curl|wget)\b|requests\.(?:get|post|put|patch|delete|Session)|urllib\.request|\bhttpx\b|"
                       r"\bfetch\s*\(|\baxios\b|http\.client|\bsocket\.|\bwebsocket", re.I)
_NET_DOC = re.compile(r"\b(?:curl|wget|WebFetch|WebSearch)\b")
_FRONT = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*(?:\n|\Z)", re.S)
CARD_TOOL = "agentj plaza"
ASK_TIMEOUT = 200            # > serve's approval wait (ASK_TTL ≤ 120 s)
MAX_ITEMS = 40


class PermError(ValueError):
    pass


EMPTY = {"tools": [], "network": False, "exec": False, "secrets": []}
ALL = {"tools": "*", "network": True, "exec": True, "secrets": "*"}


def parse(v) -> dict:
    """A manifest `permissions` value → normalized {tools, network, exec, secrets}; PermError when malformed."""
    if not isinstance(v, dict) or set(v) - set(KEYS):
        raise PermError("permissions: an object with tools / network / exec / secrets only")
    tools, secrets = v.get("tools", []), v.get("secrets", [])
    if not (isinstance(tools, list) and len(tools) <= 40 and all(isinstance(t, str) and TOOL.fullmatch(t) for t in tools)):
        raise PermError("permissions.tools: ≤ 40 tool names")
    if not (isinstance(secrets, list) and len(secrets) <= 60 and all(isinstance(s, str) and ENV.fullmatch(s) for s in secrets)):
        raise PermError("permissions.secrets: ≤ 60 environment variable names")
    for k in ("network", "exec"):
        if k in v and not isinstance(v[k], bool):
            raise PermError(f"permissions.{k}: true or false")
    return {"tools": sorted(set(tools)), "network": v.get("network", False), "exec": v.get("exec", False), "secrets": sorted(set(secrets))}


def _front_tools(text: str, key: str) -> list[str]:
    m = _FRONT.match(text)
    if not m:
        return []
    lines = m.group(1).split("\n")
    for i, ln in enumerate(lines):
        mm = re.match(rf"^{key}\s*:\s*(.*)$", ln)
        if not mm:
            continue
        raw = mm.group(1).strip()
        if not raw:                                       # a YAML list on the next lines
            items = []
            for nxt in lines[i + 1:]:
                n = re.match(r"^\s*-\s*(.+)$", nxt)
                if not n:
                    break
                items.append(n.group(1))
        else:
            items = re.split(r"[,\s]+", raw.strip("[]"))
        out = []
        for it in items:
            name = re.sub(r"\(.*", "", it.strip().strip("\"'"))
            if TOOL.fullmatch(name or ""):
                out.append(name)
        return out
    return []


def infer(manifest: dict, files: dict) -> dict:
    """The declaration a package without `permissions` gets (see the module text)."""
    tools: set = set()
    exec_, net = bool((manifest.get("install") or {}).get("verify")), False
    for p, data in files.items():
        low = p.lower()
        if p.split("/")[0] in EXEC_DIRS or (p.startswith("scaffold/") and p.split("/")[1:2] and p.split("/")[1] in EXEC_DIRS) \
                or low.endswith(SCRIPT_EXT) or data[:2] == b"#!":
            exec_ = True
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if (_NET_DOC if low.endswith(_DOC_EXT) else _NET_CODE).search(text):
            net = True
        if p.endswith("SKILL.md"):
            tools.update(_front_tools(text, "allowed-tools"))
        elif "/.claude/agents/" in f"/{p}" and p.endswith(".md"):
            tools.update(_front_tools(text, "tools"))
    if exec_:
        tools.add("Bash")
    env = ((manifest.get("requires") or {}).get("env") or []) if isinstance(manifest.get("requires"), dict) else []
    secrets = {e["name"] for e in env if isinstance(e, dict) and e.get("secret") is True and isinstance(e.get("name"), str)
               and ENV.fullmatch(e["name"])}
    return {"tools": sorted(tools), "network": net, "exec": exec_, "secrets": sorted(secrets)}


def effective(manifest: dict, files: dict) -> tuple[dict, bool, list[dict]]:
    """(what install compares, inferred?, under) — security review S1: the manifest's own declaration (PermError if malformed)
    united with infer(); `under` = the inferred items the declaration leaves out ([] when it covers them or when there is no
    declaration)."""
    inf = infer(manifest, files)
    if "permissions" not in manifest:
        return inf, True, []
    d = parse(manifest["permissions"])
    return merge(d, inf), False, excess(inf, d)


# ------------------------------------------------------------------ what this computer already allows
def _home() -> pathlib.Path:
    return pathlib.Path(os.path.expanduser("~"))


def _read_json(p: pathlib.Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def allowed(harness: str) -> tuple[dict | None, str]:
    """(allowed set | None = cannot tell, where it came from) for this harness — see the module text."""
    home = _home()
    if harness == "claude_code":
        tools, bypass, seen = set(), False, []
        for name in ("settings.json", "settings.local.json"):
            j = _read_json(home / ".claude" / name)
            if not isinstance(j, dict):
                continue
            seen.append(f"~/.claude/{name}")
            perm = j.get("permissions") if isinstance(j.get("permissions"), dict) else {}
            bypass = bypass or perm.get("defaultMode") == "bypassPermissions"
            for r in perm.get("allow") or [] if isinstance(perm.get("allow"), list) else []:
                if isinstance(r, str):
                    m = re.fullmatch(r"([A-Za-z][A-Za-z0-9_]*)(?:\(\*?\))?", r.strip())
                    if m:
                        tools.add(m.group(1))
        if not seen:
            return None, "~/.claude/settings.json"
        if bypass:
            return dict(ALL), " + ".join(seen) + " (bypassPermissions)"
        return {"tools": sorted(tools), "network": bool({"WebFetch", "Bash"} & tools), "exec": "Bash" in tools,
                "secrets": []}, " + ".join(seen)
    if harness == "codex":
        try:
            c = tomllib.loads((home / ".codex" / "config.toml").read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            return None, "~/.codex/config.toml"
        mode, policy = c.get("sandbox_mode"), c.get("approval_policy")
        if mode == "danger-full-access" and policy == "never":
            return dict(ALL), "~/.codex/config.toml (danger-full-access, approval never)"
        if mode == "workspace-write":
            ww = c.get("sandbox_workspace_write") if isinstance(c.get("sandbox_workspace_write"), dict) else {}
            return {"tools": "*", "network": ww.get("network_access") is True, "exec": True, "secrets": []}, \
                "~/.codex/config.toml (workspace-write)"
        return {"tools": [], "network": False, "exec": False, "secrets": []}, f"~/.codex/config.toml ({mode or 'read-only'})"
    return None, harness


def _has(pool, x) -> bool:
    return pool == "*" or x in (pool or [])


def excess(decl: dict, allow: dict | None, approved: dict | None = None) -> list[dict]:
    """Every declared item outside (allowed ∪ what the owner approved before for this package) → [{kind, name}]."""
    a = allow or EMPTY
    b = approved or EMPTY
    out = []
    for t in decl["tools"]:
        if t not in ALWAYS and not _has(a["tools"], t) and not _has(b.get("tools"), t):
            out.append({"kind": "tool", "name": t})
    for k in ("network", "exec"):
        if decl[k] and not a[k] and not b.get(k):
            out.append({"kind": k, "name": k})
    for s in decl["secrets"]:
        if not _has(a["secrets"], s) and not _has(b.get("secrets"), s):
            out.append({"kind": "secret", "name": s})
    return out[:MAX_ITEMS]


def merge(*sets) -> dict:
    tools, secrets, net, ex = set(), set(), False, False
    for s in sets:
        if not s:
            continue
        tools.update(s.get("tools") or [])
        secrets.update(s.get("secrets") or [])
        net, ex = net or bool(s.get("network")), ex or bool(s.get("exec"))
    return {"tools": sorted(tools), "network": net, "exec": ex, "secrets": sorted(secrets)}


_TOOL_WORDS = {
    "Bash": ("运行命令行（Bash）：能在你的电脑上执行任何命令", "run shell commands (Bash): can run any command on your computer"),
    "Write": ("新建或覆盖你电脑上的文件（Write）", "create or overwrite files on your computer (Write)"),
    "Edit": ("修改你电脑上的文件（Edit）", "change files on your computer (Edit)"),
    "MultiEdit": ("修改你电脑上的文件（MultiEdit）", "change files on your computer (MultiEdit)"),
    "NotebookEdit": ("修改笔记本文件（NotebookEdit）", "change notebook files (NotebookEdit)"),
    "WebFetch": ("打开网页、读取网上的内容（WebFetch）", "open web pages and read them (WebFetch)"),
    "WebSearch": ("上网搜索（WebSearch）", "search the web (WebSearch)"),
}


def item_text(it: dict) -> tuple[str, str]:
    k, n = it["kind"], it["name"]
    if k == "tool":
        return _TOOL_WORDS.get(n, (f"使用工具 {n}", f"use the tool {n}"))
    if k == "network":
        return ("连接互联网（可以下载东西，也可以把数据发出去）", "reach the internet (download things, or send data out)")
    if k == "exec":
        return ("运行包里自带的脚本或程序", "run the scripts or programs that come with the package")
    return (f"读取密钥 {n}", f"read the secret {n}")


def describe(decl: dict) -> str:
    parts = ([f"tools {', '.join(decl['tools'])}"] if decl["tools"] else []) + (["network"] if decl["network"] else []) \
        + (["exec"] if decl["exec"] else []) + ([f"secrets {', '.join(decl['secrets'])}"] if decl["secrets"] else [])
    return " · ".join(parts) or "无 / none"


# ------------------------------------------------------------------ the card (CLI side → serve)
def ask_owner(st, name: str, version: str, sha: str, items: list[dict], *, call=None, under: bool = False,
              publisher: dict | None = None, audit: list | None = None) -> dict | None:
    """Ask serve for the signed approval card. → serve's answer ({ok, approved?, rid?, device?, why?}) or None (serve is not
    running: no card can reach the phone). `under`: the package declared less than its content shows; `publisher`:
    {"prev": fp | None, "new": fp | None} when the publisher changed (None = unsigned); `audit`: an officially signed package's
    block findings [{rule, path, line}] (Jarvis ruling 3 — allowed once, for this sha)."""
    from . import names
    req = {"cmd": "plaza_perm", "name": name, "version": version, "sha256": sha,
           "items": [{"kind": i["kind"], "name": i["name"]} for i in items], "under": bool(under)}
    if publisher is not None:
        req["publisher"] = {"prev": publisher.get("prev"), "new": publisher.get("new")}
    if audit:
        req["audit"] = [{"rule": a["rule"], "path": a["path"], "line": a["line"]} for a in audit]
    try:
        return (call or names.ctl_call)(st, req, ASK_TIMEOUT)
    except names.ServeBusy:
        return {"ok": False, "why": "busy"}


def _fp_text(fp, en: bool) -> str:
    return fp if fp else ("unsigned" if en else "未签名")


def card_text(name: str, version: str, items: list[dict], en: bool, under: bool = False, publisher: dict | None = None,
              audit: list | None = None) -> str:
    out = [f"Plaza install needs your yes (asked by the Agent): {name} {version}" if en
           else f"广场安装要你确认（Agent 发起）：{name} {version}"]
    if publisher is not None:
        out += (["⚠ THE PUBLISHER CHANGED — this computer saw another publisher for this package name before. A new computer of "
                 "the author, or someone else using the name.", f"old: {_fp_text(publisher.get('prev'), True)}",
                 f"new: {_fp_text(publisher.get('new'), True)}"] if en else
                ["⚠ 发布方换了：这台电脑以前见到这个包名时是别的发布方。可能是作者换了电脑，也可能是别人冒名。",
                 f"旧指纹：{_fp_text(publisher.get('prev'), False)}", f"新指纹：{_fp_text(publisher.get('new'), False)}"])
    if items:
        out.append("Beyond what your Agent may do now:" if en else "超出你现在允许 Agent 做的：")
        if under:
            out.append("(the package declares less than its content shows — the items found in the content are listed too)" if en
                       else "（包声明的少于实际内容：按内容找到的也列在下面）")
        out += [("• " + item_text(i)[1 if en else 0]) for i in items]
    if audit:
        from .plaza_audit import rule_text
        out.append("The static audit refuses this officially signed package (the signature does not exempt it):" if en
                   else "静态审计拦下了这个官方签名包（官方签名不豁免）：")
        out += [f"• {a['path']} line {a['line']}: {rule_text(a['rule'])[1]}" if en else
                f"• {a['path']} 第 {a['line']} 行：{rule_text(a['rule'])[0]}" for a in audit]
    if en:
        what = " and ".join((["with these permissions"] if items else []) + (["trusting the new publisher"] if publisher else [])
                            + (["despite these audit findings, this one time"] if audit else []))
        out.append(f"Allow = install it {what}; deny = nothing is installed.")
    else:
        what = "、".join((["带着这些权限"] if items else []) + (["信任新的发布方"] if publisher else [])
                        + (["不顾上面这些审计发现（只这一次）"] if audit else []))
        out.append(f"允许 = {what}安装；不允许 = 什么都不装。")
    return "\n".join(out)


_FP = re.compile(r"[0-9a-f]{64}")
_APATH = re.compile(r"[^\x00-\x1f\x7f]{1,160}")


_NAME = re.compile(r"[a-z0-9][a-z0-9-]{1,39}")
_VER = re.compile(r"[0-9A-Za-z.+-]{1,64}")


async def serve_card(host, req: dict) -> dict:
    """serve's side of `plaza_perm`: rebuild the card text from the item kinds / names (never from free text), raise one approval
    card (batch=False; a host card no approval level auto-allows), and count only an allow signed by a paired device."""
    from .serve import card_lang
    items, pub, audit = req.get("items"), req.get("publisher"), req.get("audit")
    from .plaza_audit import rule_text
    if audit is not None and not (isinstance(audit, list) and 0 < len(audit) <= MAX_ITEMS and all(
            isinstance(a, dict) and set(a) == {"rule", "path", "line"} and isinstance(a["rule"], str) and rule_text(a["rule"])
            and isinstance(a["path"], str) and _APATH.fullmatch(a["path"]) and type(a["line"]) is int and 0 <= a["line"] < 10**7
            for a in audit)):
        return {"ok": False, "why": "bad_request"}
    if pub is not None and not (isinstance(pub, dict) and set(pub) <= {"prev", "new"}
                                and all(pub.get(k) is None or (isinstance(pub.get(k), str) and _FP.fullmatch(pub[k]))
                                        for k in ("prev", "new")) and pub.get("prev") != pub.get("new")):
        return {"ok": False, "why": "bad_request"}
    if not (isinstance(req.get("name"), str) and _NAME.fullmatch(req["name"]) and isinstance(req.get("version"), str)
            and _VER.fullmatch(req["version"]) and isinstance(items, list) and len(items) <= MAX_ITEMS
            and (items or pub is not None or audit) and isinstance(req.get("under", False), bool)
            and all(isinstance(i, dict) and i.get("kind") in ("tool", "network", "exec", "secret") and isinstance(i.get("name"), str)
                    and (TOOL.fullmatch(i["name"]) or ENV.fullmatch(i["name"])) for i in items)):
        return {"ok": False, "why": "bad_request"}
    items = [{"kind": i["kind"], "name": i["name"]} for i in items]
    pub = {"prev": pub.get("prev"), "new": pub.get("new")} if pub is not None else None
    audit = [{"rule": a["rule"], "path": a["path"], "line": a["line"]} for a in audit] if audit else None
    under = bool(req.get("under")) and bool(items)
    en = card_lang(getattr(host, "lang", None)) == "en"
    host.st.log("plaza_perm_card", name=req["name"], n=len(items), publisher=pub is not None, audit=len(audit or ()))
    ans = await host.ask(CARD_TOOL, {"summary": card_text(req["name"], req["version"], items, en, under, pub, audit),
                                     "package": req["name"], "version": req["version"], "items": items,
                                     **({"publisher": pub} if pub else {}), **({"audit": audit} if audit else {})},
                         batch=False, grant_info=True)
    g = ans.get("grant_info") if isinstance(ans, dict) else None
    if ans.get("behavior") != "allow" or not isinstance(g, dict):
        host.st.log("plaza_perm_card_done", name=req["name"], result="deny")
        return {"ok": False, "why": "denied"}
    host.st.log("plaza_perm_card_done", name=req["name"], result="allow")
    return {"ok": True, "approved": True, "rid": g.get("rid"), "device": g.get("device")}
