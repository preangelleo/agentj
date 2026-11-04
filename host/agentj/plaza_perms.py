"""Plaza permission declarations, content inference and signed escalation approvals (P426 #181-1).
Effective permissions are declared union inferred; omission cannot bypass a card. Unknown manifest fields are refused.
Read-only native permission sources define the allowed set; secrets require approval unless already under full access.
Only a paired signed owner decision accepts extra permissions or a publisher change; no serve means refusal. Stored
grants permit only later versions' already-approved scope. Publisher fingerprints remain bound to the same card.

Protocol/history detail: agentjarvis/ARCHITECTURE.md, P0261 source notes / plaza_perms.py.
"""
from __future__ import annotations

import contextlib
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
    "Bash": ("运行命令行（Bash）：能在你的 Agent J 主机上执行任何命令", "run shell commands (Bash): can run any command on your computer"),
    "Write": ("新建或覆盖你的 Agent J 主机上的文件（Write）", "create or overwrite files on your computer (Write)"),
    "Edit": ("修改你的 Agent J 主机上的文件（Edit）", "change files on your computer (Edit)"),
    "MultiEdit": ("修改你的 Agent J 主机上的文件（MultiEdit）", "change files on your computer (MultiEdit)"),
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
    try:      # #437: the card may queue behind others — serve's keep-alive / "card out" lines are skipped
        return call(st, req, ASK_TIMEOUT) if call else _call(st, req, ASK_TIMEOUT, lambda ev: None)
    except names.ServeBusy as e:
        return {**e.response(), "why": e.response()["error"]}


def _call(st, req: dict, timeout: float, progress=None) -> dict | None:
    """control.sock; inside the Agent fence (control.sock hidden) or without it, the Agent-facing elevate.sock (#428).
    None = serve is certainly not answering on either."""
    from . import fence, names
    res = None if fence.inside() else names.ctl_call(st, req, timeout, max_bytes=256 * 1024, on_sent=progress)
    if res is not None:
        return res
    from .elevate import client_request
    cmd = req["cmd"]
    res = client_request(st, {**{k: v for k, v in req.items() if k != "cmd"}, "t": cmd}, timeout=timeout,
                         on_card=progress, path=names.elevate_sock(st))
    if res.get("result") == "unavailable" and res.get("error") == "serve_busy":
        raise names.ServeBusy("TimeoutError")          # #437: there but no answer in time ≠ not running
    return None if res.get("result") == "unavailable" else res


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
                ["⚠ 发布方换了：这台 Agent J 主机以前见到这个包名时是别的发布方。可能是作者换了电脑，也可能是别人冒名。",
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


async def serve_card(host, req: dict, emit=None) -> dict:  # noqa: C901
    """serve's side of `plaza_perm`: rebuild the card text from the item kinds / names (never from free text), raise one approval
    card (batch=False; a host card no approval level auto-allows), and count only an allow signed by a paired device.
    Round 1 (M1): a gated turn is refused here, before shape checks, the card queue or a card."""
    from .serve import card_lang
    gate = turn_gate(host)
    if gate["gated"]:
        return gate_refused(host, None, req, gate["why"])
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
            and _VER.fullmatch(req["version"]) and isinstance(req.get("sha256"), str) and _FP.fullmatch(req["sha256"])
            and isinstance(items, list) and len(items) <= MAX_ITEMS
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
    return await signed_card(host, req["name"], {"summary": card_text(req["name"], req["version"], items, en, under, pub, audit)
                                                 + "\nPackage SHA-256: " + req["sha256"],
                                                 "package": req["name"], "version": req["version"], "sha256": req["sha256"],
                                                 "items": items, **({"publisher": pub} if pub else {}),
                                                 **({"audit": audit} if audit else {})}, emit)


# Host.ask's deny reason → the receipt's `why` (#422: never 「你没允许」 unless a paired device said no)
WHYS = {"device": "denied", "timeout": "timeout", "no_device": "no_device", "estop": "estop", "serve_stop": "serve_stop",
        "group_source": "group_source", "owner_grant_scope": "owner_grant_scope", "too_many": "too_many"}
CARD_QUEUE_MAX = 32          # #437: plaza cards waiting for their turn on the phone (old `--owner-confirmed` path + host requests)


class CardQueueFull(Exception):
    pass


class card_turn:
    """#437: one plaza card on the phone at a time, FIFO (asyncio.Lock), so a burst never hits serve.MAX_ASKS and each card's
    TTL starts when it really appears. While waiting, `emit` gets {"stage": "queued", "card": True, "ahead": n} (≥ every
    30 s: the CLI's socket stays alive). One lock per event loop (tests run several)."""

    def __init__(self, host, emit=None):
        self.host, self.emit = host, emit

    async def __aenter__(self):
        import asyncio
        loop = asyncio.get_running_loop()
        slot = self.host.__dict__.get("_plaza_card_slot")
        if slot is None or slot[0] is not loop:
            slot = self.host._plaza_card_slot = (loop, asyncio.Lock(), [0])
        _, self.lock, waiting = slot
        if not self.lock.locked():
            await self.lock.acquire()
            return self
        if waiting[0] >= CARD_QUEUE_MAX:
            raise CardQueueFull
        waiting[0] += 1
        acq = asyncio.ensure_future(self.lock.acquire())
        try:
            while not acq.done():
                if self.emit is not None:
                    self.emit({"stage": "queued", "card": True, "ahead": waiting[0]})
                await asyncio.wait({acq}, timeout=30)
        except BaseException:
            acq.cancel()
            if acq.done() and not acq.cancelled() and acq.exception() is None:
                self.lock.release()
            raise
        finally:
            waiting[0] -= 1
        return self

    async def __aexit__(self, *exc):
        self.lock.release()


def turn_gate(host) -> dict:
    """Who asked, read from the turn source when the request ARRIVES (round 2, L4): gated = a group / outside / OTG turn,
    refused AT ONCE by the caller with `why` (group_source / owner_grant_scope; #436/#437 round 1, M1 — never queued, never
    a card later under another turn); via = an unattended / delegated / owner-channel source (a line on the card)."""
    from . import provenance
    agent = getattr(host, "agent", None)
    if agent is None:
        return {"gated": False, "via": None, "why": None}
    via = (provenance.unattended_turn(agent) or ("delegated" if provenance.delegated_turn(agent) else None)
           or ("owner_channel" if provenance.owner_channel_turn(agent) else None))
    otg = bool(provenance.owner_grant_required(agent))
    gated = otg or bool(provenance.untrusted_turn(agent) and not via)
    from .google_api import mail_read
    return {"gated": gated, "via": via, "why": ("owner_grant_scope" if otg else "group_source") if gated else None,
            "mail": mail_read(host)}


def gate_refused(host, name, tool_input, why: str) -> dict:
    """Round 1 (M1): a gated turn's plaza request, refused without a card — logged like serve.ask's own refusal (approvals
    log: hashes and the reason only)."""
    import secrets
    from . import approvals
    with contextlib.suppress(Exception):
        approvals.record(host.st, rid=secrets.token_hex(16), agent=str(getattr(getattr(host, "agent", None), "kind", "") or ""),
                         tool=CARD_TOOL, input_sha256=approvals.input_digest(tool_input), shown_sha256=approvals.shown_digest(CARD_TOOL, ""), decision="deny",
                         reason=why)
    host.st.log("plaza_perm_card_done", name=name, result="deny", why=why)
    return {"ok": False, "why": why}


async def signed_card(host, name: str, tool_input: dict, emit=None, gate: dict | None = None) -> dict:
    """One always-asked `agentj plaza` card; only a paired device's signed allow counts (grant_info). `emit` gets
    {"stage": "card", "online", "ttl"} once the card is really out, so the caller can say where it waits. A host card (host
    text, not 「Agent 附注」, QA #1); a gated turn is refused at once (round 1, M1), not queued. #437: waits its turn
    (card_turn) first — queue full → {"ok": False, "why": "queue_full"}."""
    gate = gate or turn_gate(host)
    if gate["gated"]:
        return gate_refused(host, name, tool_input, gate["why"])
    try:
        async with card_turn(host, emit):
            return await _signed_card(host, name, tool_input, emit)
    except CardQueueFull:
        host.st.log("plaza_perm_card_done", name=name, result="deny", why="queue_full")
        return {"ok": False, "why": "queue_full"}


async def _signed_card(host, name: str, tool_input: dict, emit) -> dict:
    import asyncio
    from . import policy_registry
    asks = lambda: set(getattr(host, "asks", None) or ())
    before = asks()
    task = asyncio.ensure_future(host.ask(CARD_TOOL, tool_input, batch=False, grant_info=True, always_ask=True,
                                          _company_proposal=policy_registry.HOST_CARD))
    if emit is not None:
        for _ in range(100):
            if task.done() or asks() - before:
                break
            await asyncio.sleep(0.01)
        if not task.done() and asks() - before:
            emit({"stage": "card", "online": bool(host._online_approvers()), "ttl": int(host.ask_ttl)})
    ans = await task
    g = ans.get("grant_info") if isinstance(ans, dict) else None
    if not isinstance(ans, dict) or ans.get("behavior") != "allow" or not isinstance(g, dict):
        why = WHYS.get(ans.get("reason") if isinstance(ans, dict) else None, "no_signature")
        host.st.log("plaza_perm_card_done", name=name, result="deny", why=why)
        return {"ok": False, "why": why}
    host.st.log("plaza_perm_card_done", name=name, result="allow")
    return {"ok": True, "approved": True, "rid": g.get("rid"), "device": g.get("device")}


_ISO = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?")


class Hider:
    """Values from the Agent / the package on the host card (round 3, new-M1). Only a WORD that looks like a key is hidden —
    privacy's credential patterns (owner_text), the local SecretIndex, and for Agent values market.literal_secret (never an
    ISO date / time) — as 〔已隐藏 N 字符 · xxxxxx〕 with an HMAC under a salt fresh for this card (two different values differ
    here; nothing links across cards). The rest stays visible; long values are cut visibly (L3); `n` counts the hidden words.
    The install digest binds the full values."""

    def __init__(self, en: bool, index=None):
        import secrets as _s
        self.en, self.index, self.salt, self.n = en, index, _s.token_bytes(16), 0

    def hits(self, t: str) -> bool:
        """The local SecretIndex, also with "/" removed (a secret split into path levels, round 7)."""
        return self.index is not None and (self.index.hits(t) or self.index.hits(t.replace("/", "")))

    def _secret(self, w: str, literal: bool, path: bool = False) -> bool:
        from .market import literal_secret
        from .privacy import owner_text
        if path:                  # round 7 (new-M6): owner_text rewrites the home folder → ~ — that is not a secret
            return self.hits(w)
        return not _ISO.fullmatch(w) and (owner_text(w) != w or bool(self.index is not None and self.index.hits(w))
                                          or (literal and literal_secret(w)))

    def __call__(self, v, limit: int, literal: bool = True, secret: bool = True, path: bool = False) -> str:
        import hashlib
        import hmac
        from .text import clean_line
        s, out, used = clean_line(str(v), 100000), [], 0
        for w in s.split(" "):
            if used >= limit:
                break
            if secret and w and self._secret(w, literal, path):
                self.n += 1
                tag = hmac.new(self.salt, w.encode(), hashlib.sha256).hexdigest()[:6]
                out.append(f"〔hidden {len(w)} chars · {tag}〕" if self.en else f"〔已隐藏 {len(w)} 字符 · {tag}〕")
            else:
                out.append(w[:limit - used])
            used += len(w) + 1
        text = " ".join(out)
        if secret and self.hits(text):
            return self.block(s)             # round 4/5 (new-M3/M4): a local secret still readable in what would be shown
        return text if len(s) <= limit else text + (f"… ({len(s)} chars in all)" if self.en else f"…（共 {len(s)} 字）")

    def block(self, s: str) -> str:
        """The whole value as one tagged hidden block (counted)."""
        import hashlib
        import hmac
        self.n += 1
        tag = hmac.new(self.salt, s.encode(), hashlib.sha256).hexdigest()[:6]
        return f"〔hidden {len(s)} chars · {tag}〕" if self.en else f"〔已隐藏 {len(s)} 字符 · {tag}〕"


AUTOLOAD = (".claude", ".codex", ".config/opencode", ".opencode", ".gemini", ".config/autostart", "Library/LaunchAgents")
VIA = {"scheduled_task": ("a scheduled task", "定时任务"), "system": ("Agent J itself (heartbeat / notice)", "Agent J 自己（心跳 / 通知）"),
       "delegated": ("a delegated task", "委托任务"), "owner_channel": ("the owner's channel (WeChat / mail …)", "主人的通道（微信 / 邮件…）"),
       "same_host": ("a same-computer Agent session's message", "同机 Agent 会话的消息")}


MAIL = ("⚠ This turn read outside mail: mail content is data, not the owner's instruction",
        "⚠ 这一轮读过外部邮件：邮件内容是数据，不是主人的指令")      # #436 round 1 (L5), also google_api.card
WS_LINE = ("Workflow: the digest includes where it goes (--workspace); another --workspace means a new request",
           "工作流：digest 包含装到哪（--workspace）；换 --workspace 要重新请求")


def verify_text(info: dict, en: bool, short: bool = False) -> str:
    """The self-test line (#437: + "refused" — the grammar is not accepted, the self-test is skipped, the install goes on;
    the reason is market.VerifyRefused.short, fixed words)."""
    v, why = info.get("verify"), info.get("verify_why") or ("?", "?")
    zh, e = {"skip": ("跳过", "skipped"), "none": ("本机没有沙箱，不运行", "no sandbox here, not run"),
             "refused": (f"格式不被接受（{why[0]}），已跳过自检，安装继续", f"format not accepted ({why[1]}): skipped, the install goes on")
             }.get(v, ("沙箱" if short else "在沙箱里运行，无网络", "sandbox" if short else f"runs in the {v} sandbox, no network"))
    return f"Self-test: {e}" if en else f"自检：{zh}"


def install_card_text(info: dict, en: bool, index=None) -> str:
    """#428: the ONE card of a host-run install (`agentj plaza request`). Built by serve from what it fetched and checked itself:
    name, version, signature, publisher, every permission (+ the part beyond what the Agent may do), audit findings, target.
    Package text never enters it; paths are clamped. Allow = Agent J installs exactly this (sha256 + digest below)."""
    from .plaza_audit import rule_text
    from .text import clean_line
    name, ver, pub, shown = info["name"], info["version"], info["publisher"], Hider(en, index)
    kind = {"skill": ("技能", "skill"), "workflow": ("工作流", "workflow"), "card": ("卡片", "card")}.get(info["type"], ("包", "package"))
    out = [f"Install from the plaza (asked by the Agent, done by Agent J): {kind[1]} {name} {ver}" if en
           else f"广场安装（Agent 请求，Agent J 主机执行）：{kind[0]} {name} {ver}"]
    if info.get("via") in VIA:
        out.append(("Asked during: " if en else "来源：") + VIA[info["via"]][0 if en else 1])
    if info.get("mail"):
        out.append(MAIL[0 if en else 1])
    if info["certified"]:
        out.append("Signature: Agent J official signature, verified" if en else "签名：Agent J 官方签名，已校验")
    else:
        out.append("⚠ UNREVIEWED community package: no Agent J signature, written by another user. Allow = you accept that."
                   if en else "⚠ 未审核的社群包：没有 Agent J 官方签名，内容由别的用户写。允许 = 你同意装未审核的包。")
    if pub.get("changed"):
        out += (["⚠ THE PUBLISHER CHANGED since this computer last saw this package name.",
                 f"old: {_fp_text(pub.get('prev'), True)}", f"new: {_fp_text(pub.get('fp'), True)}"] if en else
                ["⚠ 发布方换了：这台主机以前见到这个包名时是别的发布方。", f"旧指纹：{_fp_text(pub.get('prev'), False)}",
                 f"新指纹：{_fp_text(pub.get('fp'), False)}"])
    else:
        fp = pub.get("short") or _fp_text(None, en)
        seen = ("same as before" if en else "与上次相同") if pub.get("known") else ("first seen here" if en else "首次见到")
        out.append(f"Publisher: {fp} ({seen})" if en else f"发布方：{fp}（{seen}）")
    out.append(("All permissions: " if en else "全部权限：") + describe(info["perm"]))
    extra = info.get("extra") or []
    if extra:
        out.append("Beyond what your Agent may do now:" if en else "超出你现在允许 Agent 做的：")
        if info.get("under"):
            out.append("(the package declares less than its content shows)" if en else "（包声明的少于实际内容）")
        out += ["• " + item_text(i)[1 if en else 0] for i in extra[:MAX_ITEMS]]
    else:
        out.append("All within what your Agent may do now." if en else "都在你的 Agent 现在允许的范围内。")
    audit = info.get("audit") or []
    if audit:
        out.append("Static audit:" if en else "静态审计：")
        for a in audit[:30]:
            t = rule_text(a["rule"]) or (a["rule"], a["rule"])
            tag = ("BLOCK" if en else "拦截") if a.get("level") == "block" else ("note" if en else "注意")
            p = clean_line(str(a["path"]), 160)
            out.append(f"• [{tag}] {p} line {a['line']}: {t[1]}" if en else f"• [{tag}] {p} 第 {a['line']} 行：{t[0]}")
        if any(a.get("level") == "block" for a in audit):
            out.append("Allow = install despite the blocked findings, this one time." if en else "允许 = 不顾拦截项安装（只这一次）。")
    else:
        out.append("Static audit: nothing found" if en else "静态审计：没发现问题")
    if info.get("downgrade"):
        out.append("⚠ " + clean_line(info["downgrade"], 240))
    params = list((info.get("params") or {}).items())
    joined = "".join("".join(str(v).split()) for _, v in params) + "".join(str(info.get("sign_as") or "").split())
    spread = index is not None and bool(joined) and index.hits(joined)    # round 5 (Info-1): one secret across values
    if params:
        out.append(("Parameters: " if en else "参数：") + (shown.block(joined) if spread else "; ".join(
            f"{shown(k, 40)} = {shown(v, 80)}" for k, v in params[:20])
                   + ((f"; {len(params) - 20} more" if en else f"；另有 {len(params) - 20} 个") if len(params) > 20 else "")))
    if info.get("sign_as"):
        who = shown.block(str(info["sign_as"])) if spread else shown(info["sign_as"], 40)
        out.append(f"⚠ The workflow documents get signed in the name of 「{who}」" if en else f"⚠ 工作流文档将以「{who}」的名义签字生效")
    files = info.get("files") or []
    if files:
        out.append((f"Writes {len(files)} files: " if en else f"将写入 {len(files)} 个文件：") + ", ".join(
            shown(f, 80, literal=False) for f in files[:15]) + ((f"; {len(files) - 15} more" if en else f"；另有 {len(files) - 15} 个")
                                                     if len(files) > 15 else ""))
    if info.get("verify"):
        out.append(verify_text(info, en))
    if info.get("deps"):                    # #466: known before the allow; installs anyway, the Agent adds them after
        d = ", ".join(clean_line(str(x), 40) for x in info["deps"][:10])
        out.append(f"Missing dependencies (not a blocker): {d} — the Agent adds them after the install, then reruns the self-check"
                   if en else f"缺的依赖（不阻断安装）：{d}——装好后 Agent 补齐，再重跑自检")
    tgt = shown(str(info["target"]), 300, path=True)        # SecretIndex only (round 7): the path stays readable
    out.append((f"Installs to ({info['harness']}): {tgt}" if en else f"装到（{info['harness']}）：{tgt}")
               + ((" — replaces the old one (kept in backups)" if en else "（替换旧版，旧的移到备份）") if info.get("replace") else ""))
    if info["type"] == "workflow":
        out.append(WS_LINE[0 if en else 1])
    import os
    home = os.path.realpath(os.path.expanduser("~"))
    if info["type"] != "skill" and any(str(info["target"]).startswith(os.path.join(home, d) + os.sep) for d in AUTOLOAD):
        out.append("⚠ Inside an Agent program's settings / autostart folder: things there can load by themselves" if en
                   else "⚠ 装进了 Agent 程序的配置 / 自启动目录：那里的东西可能被自动加载")
    if shown.n:
        out.append(f"⚠ {shown.n} item(s) hidden because they look like keys; if you do not recognise them, deny" if en
                   else f"⚠ 有 {shown.n} 处内容因像密钥被隐藏；不认识就拒绝")
    out.append("Allow = Agent J installs exactly this; deny = nothing is installed." if en
               else "允许 = Agent J 主机按上面内容安装；不允许 = 什么都不装。")
    out.append("Package SHA-256: " + info["sha256"])
    out.append("Install digest: " + info["digest"])
    return "\n".join(out)


def group_card_text(infos: list, en: bool, index=None, failed=(), gsha: str = "") -> str:
    """#437: ONE card for `agentj plaza request a b c` — two lines per package (kind name version · signature · publisher /
    beyond-the-Agent permissions · audit blocks · self-test · target) + its digest, the packages that failed before the card
    (name + fixed reason), and the group SHA-256 binding each name / version / sha256 / digest. Package text never enters."""
    from .text import clean_line
    shown = Hider(en, index)
    k = {"skill": ("技能", "skill"), "workflow": ("工作流", "workflow"), "card": ("卡片", "card")}
    out = [f"Install from the plaza (asked by the Agent, done by Agent J): a group of {len(infos)} packages, one card" if en
           else f"广场安装（Agent 请求，Agent J 主机执行）：一组 {len(infos)} 个包，一张卡批准"]
    if infos[0].get("via") in VIA:
        out.append(("Asked during: " if en else "来源：") + VIA[infos[0]["via"]][0 if en else 1])
    if infos[0].get("mail"):
        out.append(MAIL[0 if en else 1])
    warn = False
    for n, i in enumerate(infos, 1):
        pub = i["publisher"]
        sig = ("official, verified" if en else "官方签名已校验") if i["certified"] else ("⚠ UNREVIEWED community" if en else "⚠ 未审核社群包")
        pubt = (f"⚠ publisher changed {_fp_text(pub.get('prev'), True)[:12]}→{_fp_text(pub.get('fp'), True)[:12]}" if en else
                f"⚠ 发布方换了 {_fp_text(pub.get('prev'), False)[:12]}→{_fp_text(pub.get('fp'), False)[:12]}") if pub.get("changed") else \
            (("publisher " if en else "发布方 ") + (pub.get("short") or _fp_text(None, en))
             + ((" (same)", "（同上次）") if pub.get("known") else (" (first seen)", "（首次）"))[0 if en else 1])
        extra = [x["name"] for x in i.get("extra") or []]
        ex = (", " if en else "、").join(extra[:6]) + ((f" +{len(extra) - 6}") if len(extra) > 6 else "") if extra else ("none" if en else "无")
        blk = sum(1 for a in i.get("audit") or () if a.get("level") == "block")
        warn = warn or bool(blk) or not i["certified"] or pub.get("changed")
        tgt = shown(str(i["target"]), 160, path=True)
        kd = k.get(i["type"], ("包", "package"))[1 if en else 0]
        out.append(f"{n}. {kd} {i['name']} {i['version']} · {sig} · {pubt}")
        out.append("   " + (f"beyond your Agent: {ex} · audit blocks {blk} · " if en else f"超出权限：{ex} · 审计拦截 {blk} · ")
                   + verify_text(i, en, short=True) + " · " + (f"to ({i['harness']}): {tgt}" if en else f"装到（{i['harness']}）：{tgt}")
                   + ((" (replaces)" if en else "（替换旧版）") if i.get("replace") else ""))
        out.append(f"   digest {i['digest']} · sha256 {i['sha256'][:16]}…"
                   + (" · ⚠ " + clean_line(i["downgrade"], 80) if i.get("downgrade") else ""))
    if failed:
        out.append(("Failed before this card (not on it): " if en else "卡前没通过、不在这张卡上：")
                   + (", " if en else "、").join(f"{n} ({w})" if en else f"{n}（{w}）" for n, w in failed))
    if shown.n:
        out.append(f"⚠ {shown.n} item(s) hidden because they look like keys; if you do not recognise them, deny" if en
                   else f"⚠ 有 {shown.n} 处内容因像密钥被隐藏；不认识就拒绝")
    if warn:
        out.append("⚠ Allow also accepts every ⚠ / audit block above, this one time." if en
                   else "⚠ 允许 = 也接受上面每个 ⚠ 和审计拦截项（只这一次）。")
    out.append("Allow = Agent J installs each package above, one by one; deny = none is installed." if en
               else "允许 = Agent J 主机按上面内容逐个安装；不允许 = 一个都不装。")
    out.append("Group SHA-256: " + gsha)
    return "\n".join(out)
