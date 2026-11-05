"""Agent J market; developer reference: documentation/notes/P0262-HOST-REFERENCE.md."""
from __future__ import annotations

import collections
import contextlib
import datetime
import hashlib
import html
import json
import os
import pathlib
import re
import secrets
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
import unicodedata
import urllib.parse

from . import bundle, cloud, fence, market_guard, minisign, plaza, plaza_audit, plaza_deps, plaza_perms, plaza_trust, privacy
from .plaza import FENCE_CLOSE, FENCE_OPEN, META, NOTE, PlazaError, escape_data, json_out, plaza_clean
from .text import clean_line

EXIT_OK, EXIT_ERROR, EXIT_BLOCKED, EXIT_UNAVAILABLE, EXIT_DIGEST, EXIT_SIGNATURE = 0, 1, 2, 3, 4, 5
EXIT_PERMISSION = 6          # P426 #181-1: the package needs more than the Agent may do and the owner did not allow it
PKG_REASONS = ("spam", "malware", "privacy", "injection", "license", "other")
HARNESSES = bundle.HARNESSES
SORTS = ("new", "installs", "likes", "week")
CERT_BADGE = "【Agent J 官方审核 ✓ 签名已在本机核验 / reviewed by Agent J: signature verified on this machine】"
UNVERIFIED = "【社群 · 待审核 unverified：没有 Agent J 官方签名 / community · pending review, no Agent J signature】"
VERIFY_TIMEOUT = 60
VERIFY_MAX = 600
DRAFT_MAX = 20000
PARAM_MAX = 2000
PARAMS_FILE_MAX = 64 * 1024
CACHE_KEEP = 20
_NAME = bundle.NAME
_DIGEST = re.compile(r"[0-9a-f]{16}")
_ALIAS = re.compile(r"co-[0-9a-f]{6}")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_PH = re.compile(r"\{\{([a-z][a-z0-9_]*)\}\}")
_VERIFIED = re.compile(r"^([ \t]*(?:[-*][ \t]+)?)verified:[ \t]*\[.*\{\{[a-z][a-z0-9_]*\}\}.*\][ \t]*$", re.M)
_SIGNER = re.compile(r"human:\{\{[a-z][a-z0-9_]*\}\}")
_SIGN_AS = re.compile(r"[^\W_][\w .·-]{0,39}")
_PARAM_KEY = re.compile(r"[a-z][a-z0-9_]{0,63}")
_EXEC_DIRS = frozenset(("scripts", "cli", "tools", "bin"))
_SHELL_CHARS = frozenset(";|&$`<>(){}[]*?!~#\\'\"\n\r\t\x0b\x0c ")
_DISCARD = frozenset((">/dev/null", "2>/dev/null", "2>&1", "&>/dev/null", "1>/dev/null"))
_NO_RUN = frozenset(("sudo", "su", "doas", "pkexec", "env", "xargs", "eval", "exec", "nohup", "setsid", "curl", "wget", "nc",
                     "ncat", "socat", "ssh", "scp", "sftp", "rsync", "ftp", "telnet", "chmod", "chown", "rm", "dd", "crontab",
                     "at", "systemctl", "launchctl", "osascript", "open", "xdg-open", "agentj",
                     # verify is a quick self-test, not a build or a dependency install
                     "make", "gmake", "cmake", "ninja", "meson", "npm", "npx", "pnpm", "yarn", "bun", "pip", "pip3", "pipx",
                     "uv", "uvx", "poetry", "cargo", "go", "gradle", "mvn", "bwrap", "unshare", "sandbox-exec", "nsenter",
                     "chroot", "docker", "podman"))
_NO_MODULE = frozenset(("pip", "ensurepip", "venv", "http.server", "smtpd"))   # `python3 -m <these>`
# contract §9 reserved names: only a package signed by a key compiled into agentj may use them (name or skill_dir_name)
RESERVED = re.compile(r"^(agentj|agent-j|agentjarvis|agent-jarvis|jarvis|official|admin|moderator|plaza)(-|$)")
NO_SANDBOX = "本机没有可用的沙箱，没有运行包的自检"
VERIFY_SKIPPED = ("自检格式不被接受（{}），已跳过自检", "the self-test's format is not accepted ({}): skipped")      # #437
WS_DIGEST = ("工作流：digest 包含装到哪（--workspace）；换 --workspace 要重新预览拿新 digest / a workflow's digest includes where it "
             "goes (--workspace): another --workspace needs a new preview and its new digest")
_INLINE = re.compile(r"(?:ba|da|z|k|fi)?sh|python[0-9.]*|node|perl|ruby|php|deno|bun|lua")
TRACK_TEXT = {
    "official": "官方（服务器标记；签名在安装时于本机核验）/ official (server flag; the signature is checked on this machine at install)",
    "certified": "社群 · Agent J 官方审核（服务器标记，安装时本机核验签名）/ community · reviewed by Agent J (server flag, signature checked at install)",
    "community": "社群 · 待审核 / community · pending review",
}

ERRORS = {
    **plaza.ERRORS,
    "not_found": "广场里没有这个包（或已下架 / 待复核）/ no such package (or removed / held for review)",
    "own_package": "不能举报自己账号发的包 / you cannot report your own account's package",
    "official_package": "官方包不能举报，有问题请发反馈 / official packages cannot be reported: send feedback instead",
    "name_taken": "这个包名已经被别的账号（或官方）用了 / this package name belongs to another account (or is official)",
    "version_exists": "这个版本已经发过了：改 manifest.version / this version exists: bump manifest.version",
    "version_not_newer": "版本号必须大于已发布的最新版本 / the version must be greater than the latest published one",
    "too_large": "包太大（≤ 2 MiB）/ the package is too large (≤ 2 MiB)",
    "secret_found": "服务器的密钥扫描拦下了 / the server's secret scan refused it",
    "bad_request": "服务器不接受这个请求 / the server refused the request",
    "bad_host_sig": "服务器认为发布方签名不对，没发 / the server says the publisher signature is wrong: not published",
    "audit_blocked": "服务器的静态审计拦下了 / the server's static audit refused it",
}


class SignatureError(PlazaError):
    """Exit 5."""


# ------------------------------------------------------------------ calls
def _call(st, route: str, fields: dict, post=None) -> dict:
    for attempt in range(1, 4 if route == "get" else 2):
        try:
            status, obj = cloud.plaza_pkg_call(st, route, fields, **({"post": post} if post else {}))
        except cloud.CloudError as e:
            raise PlazaError(ERRORS.get(e.kind) or f"连不上 Agent J 服务器 / cannot reach the Agent J server ({e.kind})")
        if route != "get" or status not in (500, 502, 503, 504):
            if route == "get" and attempt > 1:
                with contextlib.suppress(Exception):
                    st.log("plaza_get_retry_finished", count=attempt, status=status)
            break
        with contextlib.suppress(Exception):
            st.log("plaza_get_retry" if attempt < 3 else "plaza_get_retry_exhausted", count=attempt, status=status)
        if attempt == 3:
            break
        time.sleep((.5, 1.5)[attempt - 1])
    if 200 <= status < 300:
        return obj
    e = cloud.parse_error(obj) or f"http_{status}"
    extra = ""
    if e == "secret_found":
        extra = f" — {clean_line(str(obj.get('path', obj.get('field', ''))), 120)}: {clean_line(str(obj.get('kind', '')), 40)}"
    raise PlazaError(f"{ERRORS.get(e, e)}{extra} (HTTP {status})")


def _state():
    return plaza._state()


def _nonce() -> str:
    return secrets.token_urlsafe(16)[:22]


# ------------------------------------------------------------------ whitelisted answers
def _int(v) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 2**53 else None


def _s(v, limit: int, one_line=True) -> str:
    return plaza_clean(v, one_line)[:limit] if isinstance(v, str) else ""


def parse_author(a) -> dict:
    a = a if isinstance(a, dict) else {}
    kind = a.get("kind") if a.get("kind") in ("official", "agent", "staff") else "agent"
    company = a.get("company") if isinstance(a.get("company"), str) and _ALIAS.fullmatch(a["company"]) else None
    name = plaza.display_name(a.get("agent_name")) or None
    return {"kind": kind, "company": company, "agent_name": name or None, "mine": a.get("mine") is True}


def parse_item(x) -> dict | None:
    """An `Item` (contract §4) through a whitelist; None when the name / type is not well-formed."""
    if not isinstance(x, dict) or not (isinstance(x.get("name"), str) and _NAME.fullmatch(x["name"])) \
            or x.get("type") not in bundle.TYPES:
        return None
    t, s = x.get("title") if isinstance(x.get("title"), dict) else {}, x.get("summary") if isinstance(x.get("summary"), dict) else {}
    tags = [clean_line(g, 24) for g in x.get("tags", [])[:12] if isinstance(g, str)] if isinstance(x.get("tags"), list) else []
    ver = x.get("verification") if isinstance(x.get("verification"), dict) else None
    verification = ({"level": ver.get("level") if ver.get("level") in ("offline", "live-key") else "offline",
                     "date": ver["date"] if isinstance(ver.get("date"), str) and _DATE.fullmatch(ver["date"]) else None}
                    if ver else None)
    return {"name": x["name"], "type": x["type"], "track": "official" if x.get("track") == "official" else "community",
            "certified": x.get("certified") is True,
            "version": x["version"] if isinstance(x.get("version"), str) and bundle.SEMVER.fullmatch(x["version"]) else None,
            "title": {"zh": _s(t.get("zh"), 160), "en": _s(t.get("en"), 160)},
            "summary": {"zh": _s(s.get("zh"), 800), "en": _s(s.get("en"), 800)},
            "tags": [g for g in tags if g],
            "category": x.get("category") if x.get("category") in bundle.CATEGORIES else None,
            "likes": _int(x.get("likes")) or 0, "installs": _int(x.get("installs")) or 0,
            "installs_week": _int(x.get("installs_week")) or 0, "liked": x.get("liked") is True,
            "state": x.get("state") if x.get("state") in ("visible", "hidden", "removed") else "visible",
            "author": parse_author(x.get("author")), "verification": verification,
            "created_at": _int(x.get("created_at")), "updated_at": _int(x.get("updated_at")),
            "remove_reason": clean_line(x["remove_reason"], 200) if isinstance(x.get("remove_reason"), str) else None}


# ------------------------------------------------------------------ P195 优先推荐伙伴 (catalog FORMAT「优先推荐伙伴」)
# The server renders every partner link once (dashboard/src/partners.ts: the platform's referral on whitelisted hosts, else the
# official URL). agentj only checks the shape (https, no credentials, printable) and NEVER takes the disclosure from the answer:
# the sentences below are compiled in, so whatever the server says, every link is printed with its disclosure right next to it.
PARTNER_DISCLOSURE = "这是 Agent J 的优先推荐伙伴，可能有推荐返佣"
PARTNER_SELF = "这是 Agent J 出品方自己的免费网站"
PARTNER_KEEP = "已经有这个服务账号的，继续用已有账号，不推荐换服务 / already have an account with it? keep using it, never suggest switching"
_SLOT = re.compile(r"[a-z][a-z0-9-]{0,31}")


def _https(v) -> str | None:
    if not isinstance(v, str) or len(v) > 300 or not v.isprintable() or any(c.isspace() for c in v):
        return None
    try:
        u = urllib.parse.urlsplit(v)
        u.port  # noqa: B018 — raises on a bad port
    except ValueError:
        return None
    if u.scheme != "https" or not u.hostname or u.username or u.password or any(c in v for c in "<>\"'`"):
        return None
    return v


def parse_partners(v) -> list[dict]:
    """`partners` of a Detail / of the partners route through a whitelist; a bad entry is dropped, never repaired."""
    out = []
    for p in (v[:40] if isinstance(v, list) else []):
        if not isinstance(p, dict) or not (isinstance(p.get("slot"), str) and _SLOT.fullmatch(p["slot"])):
            continue
        name, link, url = _s(p.get("name"), 60), _https(p.get("link")), _https(p.get("url"))
        if not name or not (link or url):
            continue
        why = p.get("why") if isinstance(p.get("why"), dict) else {}
        alts = []
        for a in (p.get("alternatives")[:6] if isinstance(p.get("alternatives"), list) else []):
            if isinstance(a, dict) and _s(a.get("name"), 60):
                soon = a.get("status") == "coming_soon"
                alts.append({"name": _s(a.get("name"), 60), "url": None if soon else _https(a.get("url")),
                             "note": _s(a.get("note_zh"), 200), "coming_soon": soon})
        self_owned = p.get("self_owned") is True
        out.append({"slot": p["slot"], "name": name, "link": link or url, "url": url or link,
                    "referral": p.get("referral") is True and not self_owned, "self_owned": self_owned,
                    "priority": _int(p.get("priority")) or 99,
                    "disclosure": PARTNER_SELF if self_owned else PARTNER_DISCLOSURE,
                    "why": _s(why.get("zh") or why.get("en"), 200), "alternatives": [a for a in alts if a["url"] or a["coming_soon"]]})
    return out


def partner_lines(partners: list[dict]) -> list[str]:
    """One line per partner: the link and its disclosure TOGETHER (the ElevenLabs terms: next to the link, not a footer)."""
    if not partners:
        return []
    out = [f"{META}优先推荐伙伴 preferred partners — 推荐时把下面整行原样给人：链接后面紧跟披露，不能拆开、不能只放末尾 / "
           f"when recommending, give the whole line: the disclosure stays right next to the link:"]
    for p in partners:
        alts = " · ".join(a["name"] + (f" {a['url']}" if a["url"] else "（即将推出 coming soon）") for a in p["alternatives"])
        out += _t(f"[{p['slot']}] {p['name']} {p['link']}（{p['disclosure']}）" + (f" — {p['why']}" if p["why"] else "")
                  + (f"\n    替代 alternatives: {alts}" if alts else ""))
    out.append(f"{META}{PARTNER_KEEP}")
    return out


def parse_detail(x) -> dict | None:
    """A `Detail`: the Item + display fields. The install never trusts these: it reads the manifest inside the bundle."""
    it = parse_item(x)
    if not it:
        return None
    sig = x.get("signature")
    files = [{"path": f["path"], "bytes": _int(f.get("bytes")) or 0} for f in (x.get("files") or [])[:bundle.MAX_FILES]
             if isinstance(x.get("files"), list) and isinstance(f, dict) and isinstance(f.get("path"), str)
             and _path_ok(f["path"])]
    versions = [{"version": v["version"], "created_at": _int(v.get("created_at"))} for v in (x.get("versions") or [])[:100]
                if isinstance(x.get("versions"), list) and isinstance(v, dict) and isinstance(v.get("version"), str)
                and bundle.SEMVER.fullmatch(v["version"])]
    it.update({
        "readme": plaza_clean(x["readme"])[:40000] if isinstance(x.get("readme"), str) else "",
        "sha256": x["sha256"] if isinstance(x.get("sha256"), str) and bundle.HEX64.fullmatch(x["sha256"]) else None,
        "bytes": _int(x.get("bytes")),
        "signature": sig if isinstance(sig, str) and 0 < len(sig) <= minisign.MAX_SIG else None,
        "files": files, "versions": versions,
        "license": _s(x.get("license"), 200), "summary_fields": summarize(x),
        "partners": parse_partners(x.get("partners")),
        # P426 #47: the publisher's own signature, passed through as served (checked at install, never trusted as-is)
        "host_sig": x["host_sig"] if isinstance(x.get("host_sig"), str) and len(x["host_sig"]) <= 100 else None,
        "host_pubkey": x["host_pubkey"] if isinstance(x.get("host_pubkey"), str) and len(x["host_pubkey"]) <= 100 else None,
        "host_signed_at": _int(x.get("host_signed_at")),
    })
    return it


def _path_ok(p: str) -> bool:
    try:
        bundle.check_path(p)
        return True
    except bundle.BundleError:
        return False


def summarize(m) -> dict:
    """What a package needs and brings, from a manifest (the bundle's) or a Detail (the server's), shape-checked field by
    field; every string stays author text (rendered on fenced text lines)."""
    m = m if isinstance(m, dict) else {}
    req = m.get("requires") if isinstance(m.get("requires"), dict) else {}
    inst = m.get("install") if isinstance(m.get("install"), dict) else {}
    lst = lambda v, n: [x for x in v[:n] if isinstance(x, dict)] if isinstance(v, list) else []  # noqa: E731
    strs = lambda v, n, k: [_s(x, k) for x in v[:n] if isinstance(x, str)] if isinstance(v, list) else []  # noqa: E731
    return {
        "harness": [h for h in (req.get("harness") or []) if h in HARNESSES] if isinstance(req.get("harness"), list) else [],
        "os": strs(req.get("os"), 10, 40),
        "cli": [{"name": _s(c.get("name"), 60), "version": _s(c.get("version"), 60), "optional": c.get("optional") is True}
                for c in lst(req.get("cli"), 40)],
        "env": [{"name": _s(e.get("name"), 64), "required": e.get("required") is not False, "secret": e.get("secret") is True,
                 "description": _s(e.get("description_zh") or e.get("description_en"), 400),
                 "how_to_get": _s(e.get("how_to_get"), 400)} for e in lst(req.get("env"), 60)],
        "accounts": [{"name": _s(a.get("name"), 200), "url": _s(a.get("url"), 500)} for a in lst(req.get("accounts"), 30)],
        "python_requirements": _s(req.get("python_requirements"), 200) or None,
        "skills": [s for s in (req.get("skills") or [])[:40] if isinstance(s, str) and _NAME.fullmatch(s)]
        if isinstance(req.get("skills"), list) else [],
        "roles": [{"name": _s(r.get("name"), 80), "file": _s(r.get("file"), 200), "summary": _s(r.get("summary_zh"), 400)}
                  for r in lst(m.get("roles"), 60)],
        "automations": [{"id": _s(a.get("id"), 80), "schedule": _s(a.get("schedule") or a.get("schedule_param"), 80),
                         "approval": _s(a.get("approval"), 20), "summary": _s(a.get("summary_zh"), 400)}
                        for a in lst(m.get("automations"), 60)],
        "post_install": strs(inst.get("post_install"), 20, 1000),
        "verify": _s(inst.get("verify"), 1000) or None,
    }


# ------------------------------------------------------------------ rendering (inside the fence)
def _t(s) -> list[str]:
    return plaza._text_lines(s if isinstance(s, str) else "")


def _when(ms) -> str:
    return plaza._when(ms)


def author_text(a: dict) -> str:
    if a["kind"] == "official":
        return "Agent J 官方 official (server field)"
    who = f"Agent「{escape_data(a['agent_name'])}」" if a["agent_name"] else ("员工 staff" if a["kind"] == "staff" else "Agent（未署名 unnamed）")
    return f"{who} @ {a['company'] or 'co-?'}" + (" · 你们发的 yours" if a["mine"] else "")


def _track(it: dict) -> str:
    return TRACK_TEXT["official" if it["track"] == "official" else "certified" if it["certified"] else "community"]


def _kind(t: str) -> str:
    return "技能 skill" if t == "skill" else "卡片 card" if t == "card" else "工作流 workflow"


def _item_lines(i: int, it: dict) -> list[str]:
    out = [f"{META}[{i}] {it['name']} · {_kind(it['type'])} · v{it['version'] or '?'} · {_track(it)}",
           f"{META}    ❤ {it['likes']} · 安装 installs {it['installs']}（本周 week {it['installs_week']}） · "
           f"{author_text(it['author'])} · {_when(it['updated_at'])}"
           + (" · 被举报待复核 hidden pending review" if it["state"] == "hidden" else "")
           + (" · 已被管理员下架 removed by the admin" if it["state"] == "removed" else "")]
    if it.get("remove_reason"):
        out += [f"{META}下架原因 removal reason（管理员写的 by the admin）:", *_t(it["remove_reason"])]
    out += [f"{META}标题 title:", *_t(it["title"]["zh"]), *_t(it["title"]["en"]),
            f"{META}简介 summary:", *_t(it["summary"]["zh"]), *_t(it["summary"]["en"])]
    if it["tags"]:
        out += [f"{META}标签 tags:", *_t(" · ".join(it["tags"]))]
    return out


def render_list(items: list[dict], heading: str) -> str:
    out = [heading, FENCE_OPEN, f"{META}{NOTE}"]
    for i, it in enumerate(items, 1):
        out += _item_lines(i, it)
    if not items:
        out.append(f"{META}（没有 none）")
    out.append(FENCE_CLOSE)
    return "\n".join(out)


def os_warning(declared):
    current = "darwin" if sys.platform == "darwin" else "windows" if sys.platform == "win32" else "linux"
    return (f"警告：包声明系统与本机 {current} 不匹配；可能无法使用，请发布者校正元数据。 / Warning: declared OS does not include {current}; ask the publisher to correct it."
            if declared and current not in declared and "any" not in declared else None)


def _summary_lines(s: dict) -> list[str]:
    out = [f"{META}需要 needs — Agent: " + (", ".join(s["harness"]) or "（未声明 not stated）")]
    if s["os"]:
        out += [f"{META}系统 os:", *_t(", ".join(s["os"]))]
    if warning := os_warning(s["os"]):
        out += [f"{META}{warning}"]
    if s["cli"]:
        out.append(f"{META}命令行工具 cli（你自己装 / you install them）:")
        for c in s["cli"]:
            out += _t(f"{c['name']} {c['version']}".strip() + (" （可选 optional）" if c["optional"] else ""))
    if s["python_requirements"]:
        out += [f"{META}Python 依赖文件 python requirements（agentj 不安装 / not installed by agentj）:", *_t(s["python_requirements"])]
    if s["env"]:
        out.append(f"{META}环境变量 env（值只填在你本机，绝不经广场 / values stay on your machine）:")
        for e in s["env"]:
            flags = ("密钥 SECRET" if e["secret"] else "非密钥 not secret") + (" · 必填 required" if e["required"] else " · 可选 optional")
            out.append(f"{META}  · {flags}:")
            out += _t(e["name"] + (f" — {e['description']}" if e["description"] else "") + (f"（获取 how: {e['how_to_get']}）" if e["how_to_get"] else ""))
    if s["accounts"]:
        out.append(f"{META}第三方账号 accounts（你自己注册 / you sign up yourself）:")
        for a in s["accounts"]:
            out += _t(a["name"] + (f" — {a['url']}" if a["url"] else ""))
    if s["skills"]:
        out.append(f"{META}依赖的广场技能 dependent skills（不会自动装，每个单独 install + 你的同意 / never pulled silently）: "
                   + ", ".join(s["skills"]))
    if s["roles"]:
        out.append(f"{META}角色 roles ({len(s['roles'])}):")
        for r in s["roles"]:
            out += _t(f"{r['name']} ({r['file']}) — {r['summary']}")
    if s["automations"]:
        out.append(f"{META}定时任务 automations ({len(s['automations'])})：全部休眠，agentj 不启用 / all stay DORMANT, agentj enables none:")
        for a in s["automations"]:
            out += _t(f"{a['id']} · {a['schedule']} · approval={a['approval']} — {a['summary']}")
    if s["verify"]:
        out += [f"{META}安装自检 install.verify（只在真沙箱里运行：临时副本、看不到你的家目录、无网络、空环境、60 秒、不经 shell；没有沙箱就不运行 / "
                f"runs only inside a real sandbox: throw-away copy, home hidden, no network, empty env, 60 s, no shell — "
                f"never without one）:", *_t(s["verify"])]
    if s["post_install"]:
        out.append(f"{META}安装后命令 post_install（只显示，agentj 绝不运行；要不要跑由你的人决定 / SHOWN ONLY, never run by agentj）:")
        for c in s["post_install"]:
            out += _t(c)
    return out


def render_detail(d: dict) -> str:
    out = [FENCE_OPEN, f"{META}{NOTE}", *_item_lines(1, d)]
    out.append(f"{META}版本 versions: " + (", ".join(v["version"] for v in d["versions"]) or (d["version"] or "?"))
               + (f" · {len(d['files'])} 个文件 files" if d["files"] else "") + (f" · {d['bytes']} bytes" if d["bytes"] else "")
               + (f" · sha256 {d['sha256']}" if d["sha256"] else ""))
    if d["verification"]:
        out.append(f"{META}作者自述的验证 verification (author's statement): {d['verification']['level']} {d['verification']['date'] or ''}".rstrip())
    if d["license"]:
        out += [f"{META}许可 license:", *_t(d["license"])]
    out += _summary_lines(d["summary_fields"])
    out += partner_lines(d.get("partners") or [])
    if d["files"]:
        out.append(f"{META}文件 files:")
        out += _t("\n".join(f"{f['path']} ({f['bytes']} B)" for f in d["files"]))
    out += [f"{META}README:", *_t(d["readme"])]
    out.append(FENCE_CLOSE)
    return "\n".join(out)


# ------------------------------------------------------------------ search / show / mine / like / report / installed
def search_fields(q: str, *, type_=None, sort=None, tag=None, track=None, limit=20) -> dict:
    f = {"q": q, "limit": max(1, min(50, int(limit)))}
    if type_ in bundle.TYPES:
        f["type"] = type_
    if sort:
        if sort not in SORTS:
            raise PlazaError(f"--sort: {' | '.join(SORTS)}")
        f["sort"] = sort
    if tag:
        t = plaza_clean(tag, True)
        if not 1 <= len(t) <= 24:
            raise PlazaError("--tag: 1–24 个字 / 1–24 characters")
        f["tag"] = t
    if track:
        f["track"] = track
    return f


def run_search(words, *, type_="all", sort=None, tag=None, track=None, limit=20, as_json=False, st=None, post=None, out=None) -> int:
    """`--type all` (default): packages first, then Q&A posts; `qa`: posts only; `skill` / `workflow`: packages only."""
    out = out or sys.stdout
    st = st or _state()
    q = plaza_clean(" ".join(words), True)[:200]
    pkgs, posts = None, None
    if type_ != "qa":
        obj = _call(st, "search", search_fields(q, type_=type_, sort=sort, tag=tag, track=track, limit=limit), post)
        pkgs = [p for p in (parse_item(x) for x in obj.get("items", []) if isinstance(obj.get("items"), list)) if p]
    if type_ in ("all", "qa"):
        if as_json or type_ == "all":
            o = plaza._call(st, "search", {"q": q, "limit": max(1, min(20, limit))}, post)
            posts = [p for p in (plaza.parse_item(x) for x in o.get("items", []) if isinstance(o.get("items"), list)) if p]
        else:
            return plaza.run_search(words, limit=limit, st=st, post=post, out=out)
    if as_json:
        print(json_out({"query": q, **({"packages": pkgs} if pkgs is not None else {}), **({"items": posts} if posts is not None else {})}), file=out)
        return EXIT_OK
    if pkgs is not None:
        label = {"all": "技能 + 工作流 skills + workflows", "skill": "技能 skills", "workflow": "工作流 workflows"}.get(type_, "")
        print(render_list(pkgs, f"广场{label}「{escape_data(q)}」：{len(pkgs)} 个 / plaza packages: {len(pkgs)}"), file=out)
    if posts is not None:
        print(plaza.render_list(posts, f"广场问答「{escape_data(q)}」：{len(posts)} 条 / plaza Q&A: {len(posts)} post(s)"), file=out)
    print("下一步 / next: agentj plaza show <包名 name | pz_id> · 预览 preview: agentj plaza install <name> → 主人同意后 / "
          "owner's yes: agentj plaza request <name>", file=out)
    return EXIT_OK


def _get(st, name: str, version: str | None, post) -> tuple[dict, dict]:
    if not _NAME.fullmatch(name or ""):
        raise PlazaError("包名形如 gmail-read（小写字母、数字、连字符）/ a package name looks like gmail-read")
    if version is not None and not bundle.SEMVER.fullmatch(version):
        raise PlazaError("--version: MAJOR.MINOR.PATCH")
    obj = _call(st, "get", {"name": name, **({"version": version} if version else {})}, post)
    d = parse_detail(obj.get("package"))
    if not d or d["name"] != name:
        raise PlazaError("服务器的回答看不懂 / unexpected answer")
    return d, (obj.get("download") if isinstance(obj.get("download"), dict) else {})


def run_show_pkg(name: str, *, version=None, as_json=False, st=None, post=None, out=None) -> int:
    out = out or sys.stdout
    st = st or _state()
    d, _ = _get(st, name, version, post)
    if as_json:
        print(json_out({"package": d}), file=out)
        return EXIT_OK
    print(render_detail(d), file=out)
    v = f" --version {version}" if version else ""
    print(f"预览 preview（不写任何东西 / writes nothing）: agentj plaza install {d['name']}{v} → 主人同意后 / owner's yes: "
          f"agentj plaza request {d['name']}{v}", file=out)
    return EXIT_OK


def run_partners(slot: str | None = None, *, as_json=False, st=None, post=None, out=None) -> int:
    """P195: the platform's preferred partners (official packages' defaults + the platform's overrides), one slot or all."""
    out = out or sys.stdout
    if slot is not None and not _SLOT.fullmatch(slot):
        raise PlazaError("--slot 形如 tts / voice-clone（小写字母、数字、连字符）/ a slot looks like tts")
    st = st or _state()
    obj = _call(st, "partners", {"slot": slot} if slot else {}, post)
    ps = parse_partners(obj.get("partners"))
    if slot:
        ps = [p for p in ps if p["slot"] == slot]
    ver = _int(obj.get("version")) or 0
    if as_json:
        print(json_out({"version": ver, "partners": ps, "keep_account": PARTNER_KEEP}), file=out)
        return EXIT_OK
    lines = partner_lines(ps) or [f"{META}没有优先推荐伙伴 / no preferred partner" + (f" for {slot}" if slot else "")]
    print("\n".join([FENCE_OPEN, f"{META}配置版本 config version {ver}", *lines, FENCE_CLOSE]), file=out)
    return EXIT_OK


def run_show(target: str, *, version=None, as_json=False, st=None, post=None, out=None) -> int:
    if plaza._POST_ID.fullmatch(target or ""):
        return plaza.run_show(target, as_json=as_json, st=st, post=post, out=out)
    return run_show_pkg(target, version=version, as_json=as_json, st=st, post=post, out=out)


def run_mine(*, as_json=False, st=None, post=None, out=None) -> int:
    out = out or sys.stdout
    st = st or _state()
    o = plaza._call(st, "mine", {}, post)
    posts = [p for p in (plaza.parse_item(x) for x in o.get("items", []) if isinstance(o.get("items"), list)) if p]
    obj = _call(st, "mine", {}, post)
    pkgs = [p for p in (parse_item(x) for x in obj.get("items", []) if isinstance(obj.get("items"), list)) if p]
    if as_json:
        print(json_out({"items": posts, "packages": pkgs}), file=out)
        return EXIT_OK
    print(plaza.render_list(posts, f"你们发的帖子：{len(posts)} 条 / your account's posts: {len(posts)}"), file=out)
    print(render_list(pkgs, f"你们发布的包：{len(pkgs)} 个 / your account's packages: {len(pkgs)}"), file=out)
    return EXIT_OK


def run_like(name: str, on: bool = True, *, st=None, post=None, out=None) -> int:
    out = out or sys.stdout
    if not _NAME.fullmatch(name or ""):
        raise PlazaError("包名形如 gmail-read / a package name looks like gmail-read")
    st = st or _state()
    obj = _call(st, "like", {"nonce": _nonce(), "name": name, "on": bool(on)}, post)
    likes = _int(obj.get("likes"))
    print(("已点赞 / liked" if obj.get("liked") is True else "已取消点赞 / unliked") + f": {name}"
          + (f" · ❤ {likes}" if likes is not None else ""), file=out)
    return EXIT_OK


def run_report(target: str, reason: str, *, st=None, post=None, out=None) -> int:
    out = out or sys.stdout
    if plaza._POST_ID.fullmatch(target or "") or plaza._REPLY_ID.fullmatch(target or ""):
        if reason not in plaza.REASONS:
            raise PlazaError(f"帖子 / 回复的举报原因 / reasons for posts: {', '.join(plaza.REASONS)}")
        return plaza.run_report(target, reason, st=st, post=post, out=out)
    if not _NAME.fullmatch(target or ""):
        raise PlazaError("举报对象是 pz_… / pr_… 或包名 / report a pz_… / pr_… id or a package name")
    if reason not in PKG_REASONS:
        raise PlazaError(f"包的举报原因 / reasons for packages: {', '.join(PKG_REASONS)}")
    st = st or _state()
    obj = _call(st, "report", {"nonce": _nonce(), "name": target, "reason": reason}, post)
    msg = "已经举报过了 / already reported" if obj.get("already") is True else "已举报 / reported"
    if obj.get("hidden") is True:
        msg += "；已被隐藏，等待管理员复核 / now hidden pending the admin's review"
    print(f"{msg}: {target}", file=out)
    return EXIT_OK


# ------------------------------------------------------------------ local record (<state>/plaza/installed.json)
def _plaza_dir(st) -> pathlib.Path:
    d = st.root / "plaza"
    d.mkdir(mode=0o700, exist_ok=True)
    os.chmod(d, 0o700)
    return d


def read_installed(st) -> list[dict]:
    try:
        d = json.loads((st.root / "plaza" / "installed.json").read_text())
    except (OSError, ValueError, UnicodeDecodeError):
        return []
    items = d.get("items") if isinstance(d, dict) else None
    out = []
    for r in items if isinstance(items, list) else []:
        if isinstance(r, dict) and isinstance(r.get("name"), str) and _NAME.fullmatch(r["name"]):
            out.append({k: r.get(k) for k in ("name", "type", "version", "sha256", "harness", "target", "certified", "installed_at", "category")}
                       | {k: r[k] for k in ("permissions", "perm_grant", "publisher_grant", "audit_grant", "requires", "verify") if isinstance(r.get(k), dict)})  # P426 #181-1
    return out


def _record_install(st, rec: dict) -> None:
    items = [r for r in read_installed(st) if not (r["name"] == rec["name"] and r["harness"] == rec["harness"]
                                                   and r["target"] == rec["target"])]
    items.append(rec)
    st.write_private(_plaza_dir(st) / "installed.json", json.dumps({"items": items}, ensure_ascii=False, indent=1).encode())


def run_installed(*, as_json=False, st=None, out=None) -> int:
    out = out or sys.stdout
    st = st or _state()
    items = read_installed(st)
    if as_json:
        print(json.dumps({"items": items}, ensure_ascii=False, indent=1), file=out)
        return EXIT_OK
    if not items:
        print("本机还没从广场装过包 / nothing installed from the plaza on this machine", file=out)
        return EXIT_OK
    for r in items:
        print(f"{r['name']} {r['version']} · {r['type']} · {r['harness']} · "
              + ("Agent J 官方审核 certified" if r.get("certified") else "社群 · 待审核 unverified") + f" · {r['installed_at']} · {r['target']}", file=out)
    return EXIT_OK


# ------------------------------------------------------------------ params (FORMAT §2 / §7.3; the subset the catalog uses)
_FORMATS = {
    "date": lambda v: _valid_date(v),
    "date-time": lambda v: _valid_datetime(v),
    "email": lambda v: re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", v) is not None,
    "uri": lambda v: re.fullmatch(r"[A-Za-z][A-Za-z0-9+.-]*://\S+", v) is not None,
}


def _valid_date(v: str) -> bool:
    try:
        return bool(_DATE.fullmatch(v)) and datetime.date.fromisoformat(v) is not None
    except ValueError:
        return False


def _valid_datetime(v: str) -> bool:
    try:
        datetime.datetime.fromisoformat(v.replace("Z", "+00:00"))
        return "T" in v
    except ValueError:
        return False


def _value_problem(s: str) -> str | None:
    if len(s) > PARAM_MAX:
        return f"longer than {PARAM_MAX} characters"
    if "{{" in s or "}}" in s:
        return "contains {{ or }}"
    for ch in s:
        if ch not in "\n\t" and unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Co", "Zl", "Zp"):
            return "contains a control / format character"
    return None


def check_value(name: str, spec: dict, v) -> None:
    t = spec.get("type")
    if t == "string":
        if not isinstance(v, str):
            raise PlazaError(f"参数 param {name}: 要字符串 / must be a string")
        why = _value_problem(v)
        if why:
            raise PlazaError(f"参数 param {name}: {why}")
        if isinstance(spec.get("minLength"), int) and len(v) < spec["minLength"]:
            raise PlazaError(f"参数 param {name}: 至少 {spec['minLength']} 个字 / too short")
        if isinstance(spec.get("maxLength"), int) and len(v) > spec["maxLength"]:
            raise PlazaError(f"参数 param {name}: 最多 {spec['maxLength']} 个字 / too long")
        if isinstance(spec.get("pattern"), str):
            try:
                ok = re.search(spec["pattern"], v) is not None
            except re.error:
                raise PlazaError(f"参数 param {name}: the package's pattern does not compile") from None
            if not ok:
                raise PlazaError(f"参数 param {name}: 格式不对 / does not match the pattern")
        fmt = spec.get("format")
        if isinstance(fmt, str) and fmt in _FORMATS and not _FORMATS[fmt](v):
            raise PlazaError(f"参数 param {name}: 不是有效的 {fmt} / not a valid {fmt}")
    elif t == "integer":
        if not (isinstance(v, int) and not isinstance(v, bool)):
            raise PlazaError(f"参数 param {name}: 要整数 / must be an integer")
    elif t == "number":
        if not (isinstance(v, (int, float)) and not isinstance(v, bool)) or v != v or v in (float("inf"), float("-inf")):
            raise PlazaError(f"参数 param {name}: 要数字 / must be a number")
    elif t == "boolean":
        if not isinstance(v, bool):
            raise PlazaError(f"参数 param {name}: 要 true / false")
    elif t == "array":
        if not isinstance(v, list) or len(v) > 100:
            raise PlazaError(f"参数 param {name}: 要列表 / must be a list")
        items = spec.get("items") if isinstance(spec.get("items"), dict) else {"type": "string"}
        for x in v:
            check_value(name, items, x)
        if isinstance(spec.get("minItems"), int) and len(v) < spec["minItems"]:
            raise PlazaError(f"参数 param {name}: 至少 {spec['minItems']} 项 / too few items")
        if isinstance(spec.get("maxItems"), int) and len(v) > spec["maxItems"]:
            raise PlazaError(f"参数 param {name}: 最多 {spec['maxItems']} 项 / too many items")
        if spec.get("uniqueItems") is True and len({json.dumps(x, sort_keys=True) for x in v}) != len(v):
            raise PlazaError(f"参数 param {name}: 不能重复 / items must be unique")
    else:
        raise PlazaError(f"参数 param {name}: 这个包的参数类型 agentj 不支持 / unsupported schema type {str(t)[:20]!r}")
    if t in ("integer", "number"):
        if isinstance(spec.get("minimum"), (int, float)) and v < spec["minimum"]:
            raise PlazaError(f"参数 param {name}: 不能小于 {spec['minimum']} / below the minimum")
        if isinstance(spec.get("maximum"), (int, float)) and v > spec["maximum"]:
            raise PlazaError(f"参数 param {name}: 不能大于 {spec['maximum']} / above the maximum")
    if isinstance(spec.get("enum"), list) and v not in spec["enum"]:
        raise PlazaError(f"参数 param {name}: 只能是 / one of " + ", ".join(str(e)[:40] for e in spec["enum"][:20]))


def _coerce(name: str, spec: dict, raw: str):
    """A `--param k=v` string → the schema's type."""
    t = spec.get("type")
    try:
        if t == "integer":
            return int(raw, 10)
        if t == "number":
            return float(raw) if any(c in raw for c in ".eE") else int(raw, 10)
        if t == "boolean":
            if raw.lower() not in ("true", "false"):
                raise ValueError
            return raw.lower() == "true"
        if t == "array":
            if raw.startswith("["):
                v = json.loads(raw)
                if not isinstance(v, list):
                    raise ValueError
                return v
            return [x.strip() for x in raw.split(",") if x.strip()]
    except ValueError:
        raise PlazaError(f"参数 param {name}: 不能解析为 {t} / cannot read it as {t}") from None
    return raw


def _x_auto(rule: str, harness: str, now: datetime.datetime):
    if rule == "today":
        return now.date().isoformat()
    if rule == "now":
        return now.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if rule == "harness":
        return harness
    m = re.fullmatch(r"today\+([0-9]{1,4})d", rule)
    if m:
        return (now.date() + datetime.timedelta(days=int(m.group(1)))).isoformat()
    raise PlazaError(f"这个包的 x-auto 规则 agentj 不认识 / unknown x-auto rule {rule[:30]!r}")


def read_params_file(path) -> dict:
    if isinstance(path, dict):           # #428: `agentj plaza request` read the file on the Agent's side and sent the object
        if len(json.dumps(path, ensure_ascii=False).encode()) > PARAMS_FILE_MAX:
            raise PlazaError("--params-file 太大 / too large")
        return path
    raw = plaza.read_body_file(path) if path else "{}"
    if len(raw.encode()) > PARAMS_FILE_MAX:
        raise PlazaError("--params-file 太大 / too large")
    try:
        d = json.loads(raw)
    except ValueError:
        raise PlazaError("--params-file 不是 JSON / not JSON") from None
    if not isinstance(d, dict):
        raise PlazaError("--params-file: 一个 JSON 对象 / a JSON object")
    return d


def resolve_params(schema: dict, cli_params: list[str], params_file: str | None, harness: str, now: datetime.datetime) -> tuple[dict, set]:
    """→ (values, names computed by x-auto). Unknown names, x-auto names set by hand, type / enum / pattern / format errors
    and missing required values are refused."""
    props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    given: dict = {}
    if params_file:
        for k, v in read_params_file(params_file).items():
            given[k] = ("file", v)
    for p in cli_params or []:
        k, sep, v = p.partition("=")
        if not sep or not _PARAM_KEY.fullmatch(k):
            raise PlazaError("--param 写成 name=value / --param takes name=value")
        if k in given and given[k][0] == "cli":
            raise PlazaError(f"--param {k} 给了两次 / given twice")
        given[k] = ("cli", v)
    values, auto = {}, set()
    for k, (src, v) in given.items():
        if k not in props or not isinstance(props[k], dict):
            raise PlazaError(f"这个包没有参数 / the package has no param {k[:64]!r}")
        if isinstance(props[k].get("x-auto"), str):
            raise PlazaError(f"参数 param {k} 由 agentj 在安装时计算，不能手填 / is computed by agentj at install (x-auto)")
        values[k] = _coerce(k, props[k], v) if src == "cli" else v
    for k, spec in props.items():
        if not isinstance(spec, dict):
            continue
        if isinstance(spec.get("x-auto"), str):
            values[k] = _x_auto(spec["x-auto"], harness, now)
            auto.add(k)
        elif k not in values and "default" in spec:
            values[k] = json.loads(json.dumps(spec["default"]))
    for k in schema.get("required", []) if isinstance(schema.get("required"), list) else []:
        if k not in values:
            raise PlazaError(f"缺必填参数 / missing required param {str(k)[:64]}: --param {str(k)[:64]}=…")
    for k, v in values.items():
        check_value(k, props[k], v)
    return values, auto


# ------------------------------------------------------------------ rendering (FORMAT §7.1 – 7.3)
def _as_text(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return json.dumps(v)
    if isinstance(v, list):
        return ", ".join(_as_text(x) for x in v)
    return str(v)


def _toml_escape(s: str) -> str:
    out = []
    for ch in s:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20 or ord(ch) == 0x7f:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return "".join(out)


def file_kind(path: str) -> str:
    ext = path.rsplit(".", 1)[-1].lower() if "." in path.rsplit("/", 1)[-1] else ""
    return {"json": "json", "toml": "toml", "yaml": "yaml", "yml": "yaml", "html": "html", "htm": "html", "xml": "html",
            "svg": "html"}.get(ext, "plain")


def escape_for(kind: str, s: str) -> str:
    if kind in ("json", "yaml"):          # YAML: a JSON-escaped string is valid inside YAML double quotes, and has no newline
        return json.dumps(s, ensure_ascii=False)[1:-1]
    if kind == "toml":
        return _toml_escape(s)
    if kind == "html":
        return html.escape(s, quote=True)
    return s


def render_file(path: str, text: str, values: dict, sign_as: str | None, install_date: str) -> str:
    """One scaffold text file. The signing step first (FORMAT §7.2): `verified: [ … {{…}} … ]` lines become the Owner's
    signature with --sign-as, else `verified: []`; then every `{{name}}` → the value escaped for the file type; an unknown
    or valueless name, or anything left that looks like a placeholder, aborts; every .json must parse afterwards."""
    kind = file_kind(path)

    def sig(m):
        if not sign_as:
            return m.group(1) + "verified: []"
        line, n = _SIGNER.subn(lambda _: "human:" + escape_for(kind, sign_as), m.group(0))
        return line if n else m.group(1) + f"verified: [{{ by: human:{escape_for(kind, sign_as)}, at: {install_date} }}]"
    text = _VERIFIED.sub(sig, text)

    def sub(m):
        k = m.group(1)
        if k not in values:
            raise PlazaError(f"{path}: 占位符 {{{{{k}}}}} 没有对应参数 / placeholder without a param — install aborted")
        return escape_for(kind, _as_text(values[k]))
    out = _PH.sub(sub, text)
    if _PH.search(out):
        raise PlazaError(f"{path}: 渲染后仍有 {{{{…}}}} 占位符 / a placeholder is left after rendering — install aborted")
    if kind == "json":
        try:
            json.loads(out)
        except ValueError:
            raise PlazaError(f"{path}: 渲染后不是合法 JSON / not valid JSON after rendering — install aborted") from None
    return out


_FRONT = re.compile(r"\A---[ \t]*\n(.*?\n)---[ \t]*\n(.*)\Z", re.S)


def codex_agent_toml(md: str, stem: str) -> str:
    """`.claude/agents/<role>.md` (frontmatter name / description + body) → Codex `.codex/agents/<role>.toml`, parsed back."""
    m = _FRONT.match(md)
    if not m:
        raise PlazaError(f".claude/agents/{stem}.md: 没有 frontmatter / no frontmatter — install aborted")
    fields = {}
    for line in m.group(1).splitlines():
        km = re.match(r"([A-Za-z_]+):[ \t]?(.*)$", line)
        if km:
            v = km.group(2).strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            fields[km.group(1)] = v
    name, desc, body = fields.get("name") or stem, fields.get("description", ""), m.group(2).strip("\n")
    toml = (f'name = "{_toml_escape(name)}"\ndescription = "{_toml_escape(desc)}"\n'
            f'developer_instructions = "{_toml_escape(body)}"\n')
    try:
        back = tomllib.loads(toml)
    except tomllib.TOMLDecodeError:
        raise PlazaError(f".codex/agents/{stem}.toml: does not parse — install aborted") from None
    if back != {"name": name, "description": desc, "developer_instructions": body}:
        raise PlazaError(f".codex/agents/{stem}.toml: round trip differs — install aborted")
    return toml


def codex_text(text: str) -> str:
    return text.replace(".claude/", ".agents/").replace("CLAUDE.md", "AGENTS.md")


def install_files(manifest: dict, files: dict, harness: str, values: dict, sign_as: str | None, install_date: str) -> dict:
    """{installed path: bytes} — a skill as it is (Codex: .md files converted); a workflow = scaffold/ rendered."""
    out: dict = {}
    if manifest["type"] == "skill":
        for p, data in files.items():
            if harness == "codex" and p.lower().endswith(".md"):
                try:
                    data = codex_text(data.decode("utf-8")).encode("utf-8")
                except UnicodeDecodeError:
                    pass
            out[p] = data
        return out
    for p, data in files.items():
        if not p.startswith("scaffold/"):
            continue
        rel = p[len("scaffold/"):]
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            out[rel] = data
            continue
        out[rel] = render_file(rel, text, values, sign_as, install_date).encode("utf-8")
    for rel in out:
        bundle.check_path(rel)       # package paths (a scaffold/.codex/… file is refused here and by bundle.parse)
    if harness == "codex":
        for rel in sorted(out):
            m = re.fullmatch(r"\.claude/agents/([^/]+)\.md", rel)
            if m and f".codex/agents/{m.group(1)}.toml" not in out and _FRONT.match(out[rel].decode("utf-8", "replace")):
                out[f".codex/agents/{m.group(1)}.toml"] = codex_agent_toml(out[rel].decode("utf-8"), m.group(1)).encode("utf-8")
    bundle.check_path_set(out)
    for rel in out:                  # the installer's own .codex/agents/<role>.toml: a checked role name, nothing else
        if not rel.startswith(".codex/"):
            bundle.check_path(rel)
        elif not re.fullmatch(r"\.codex/agents/[^/]+\.toml", rel) or not bundle._seg_ok(rel.split("/")[-1]):
            raise PlazaError(f"{rel}: not an installer-generated role file — install aborted")
    return out


def _mode(path: str) -> int:
    segs = path.split("/")
    if path.endswith(".sh") or (path.endswith(".py") and any(s in _EXEC_DIRS for s in segs[:-1])):
        return 0o755
    return 0o644


def write_tree(root: pathlib.Path, files: dict, dir_fd: int | None = None) -> None:
    """Into a NEW directory `root` (0755; `dir_fd`: its parent's fd, #428): every file created O_EXCL | O_NOFOLLOW, 0644
    (0755 for *.sh and *.py under scripts/ cli/ tools/ bin/), folders 0755, each entered through an O_NOFOLLOW fd (a symlink
    swapped in meanwhile is refused). Paths were checked by bundle.check_path (relative, no `..`)."""
    D = market_guard._DIR
    root = pathlib.Path(root)
    pfd = os.open(root.parent, D) if dir_fd is None else dir_fd
    fds = {}
    try:
        os.mkdir(root.name, 0o755, dir_fd=pfd)
        fds[""] = os.open(root.name, D, dir_fd=pfd)
        for p in sorted(files, key=lambda x: x.encode()):
            segs, key = p.split("/"), ""
            for s in segs[:-1]:
                sub = key + "/" + s
                if sub not in fds:
                    with contextlib.suppress(FileExistsError):
                        os.mkdir(s, 0o755, dir_fd=fds[key])
                    fds[sub] = os.open(s, D, dir_fd=fds[key])
                key = sub
            mode = _mode(p)
            fd = os.open(segs[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), mode, dir_fd=fds[key])
            try:
                os.write(fd, files[p])
                os.fchmod(fd, mode)
            finally:
                os.close(fd)
        for fd in fds.values():
            os.fchmod(fd, 0o755)
    finally:
        for fd in fds.values():
            os.close(fd)
        if dir_fd is None:
            os.close(pfd)


# ------------------------------------------------------------------ install.verify
class VerifyRefused(PlazaError):
    """#437: install.verify's grammar is refused. str = the exact reason (characters / program / argument, for the Agent's
    output); `short` = (zh, en) fixed words only (the host card: never package text)."""

    def __init__(self, msg: str, short: tuple[str, str]):
        super().__init__(msg)
        self.short = short


_META = frozenset(";|&$`<>(){}[]*?!~#\\")         # shell syntax outside quotes (#437: inside quotes = literal text)
_SHOW = {" ": "空格 space", "\t": "\\t", "\n": "\\n", "\r": "\\r"}


def _chars(cs) -> str:
    seen = list(dict.fromkeys(cs))
    return " ".join(f"`{_SHOW.get(c, c if c.isprintable() else f'U+{ord(c):04X}')}`" for c in seen[:8]) + (" …" if len(seen) > 8 else "")


def _vr(zh: str, en: str, detail: str = "") -> VerifyRefused:
    return VerifyRefused(f"install.verify {zh}{detail}，拒绝运行 / {en}{detail}: refused", (zh, en))


def _verify_files_shipped(m, files) -> None:
    """#465: a self-test naming a file the package lacks is the author's mistake: skipped like a refused grammar, never a failure."""
    miss = [p for k, p in bundle.missing_entry_files(m, files) if k == "install.verify"]
    if miss:
        raise _vr("指向包里没有的文件", "names a file the package does not have", f" {clean_line(miss[0], 60)}")


def _meta_refusal(cs) -> VerifyRefused:
    shown = _chars(cs)
    return VerifyRefused(f"install.verify 含 shell 元字符 {shown}，拒绝运行 / contains shell metacharacters {shown}: refused",
                         (f"含 shell 元字符 {shown}", f"shell metacharacters {shown}"))


def _split_quoted(cmd: str) -> list[str | None]:
    """#437, officially signed packages: quote-aware words. Outside quotes every shell syntax character is refused (named);
    '…' and "…" are literal parts of one argument (no shell runs it), except $ ` \\ inside "…" (a shell would expand those).
    `&&` → None (the separator); `>/dev/null` / `2>&1` … are dropped."""
    words, bad, i, n = [], [], 0, len(cmd)
    while i < n:
        if cmd[i] in " \t":
            i += 1
            continue
        parts, raw, rawbad = [], True, []
        while i < n and cmd[i] not in " \t":
            c = cmd[i]
            if c in "'\"":
                j = cmd.find(c, i + 1)
                if j < 0:
                    raise _vr("引号不配对", "unbalanced quotes")
                if c == '"':
                    bad += [x for x in cmd[i + 1:j] if x in "$`\\"]
                parts.append(cmd[i + 1:j])
                raw, i = False, j + 1
                continue
            if c in _META:
                rawbad.append(c)
            parts.append(c)
            i += 1
        w = "".join(parts)
        if raw and w == "&&":
            words.append(None)
        elif raw and w in _DISCARD:
            pass
        else:
            bad += rawbad
            if not w:
                raise _vr("含空参数", "an empty argument")
            words.append(w)
    if bad:
        raise _meta_refusal(bad)
    return words


def parse_verify(cmd: str, *, certified: bool = False) -> list[list[str]]:
    """`install.verify` → argv lists. No shell: `&&` between simple commands and a trailing `>/dev/null` / `2>&1` are the
    only syntax understood; absolute paths, `..`, and network / privilege / wrapper programs are refused. Unsigned packages:
    any other shell metacharacter (`;|&$\\`<>(){}[]*?!~#`, quotes that join words, backslashes, newlines) and `-c` / `-e` code
    strings are refused. #437 — officially signed (`certified`, signature checked): the same characters only OUTSIDE quotes,
    quoted text is one literal argument, and `python3 -c` / `node -e` … inline code is allowed (it is the package's own code,
    run in the same sandbox as its scripts); that code argument skips the path rule. Every refusal names what it refused."""
    if not isinstance(cmd, str) or not cmd.strip() or len(cmd) > VERIFY_MAX:
        raise _vr("是空的或太长", "is empty or too long")
    if any(unicodedata.category(c).startswith("C") and c not in " \t" for c in cmd):
        raise _vr("含控制字符或换行", "contains control characters or newlines")
    if certified:
        toks = _split_quoted(cmd)
    else:
        if any(c in cmd for c in "$`\\;|"):
            raise _meta_refusal([c for w in cmd.split() if w != "&&" and w not in _DISCARD for c in w if c in _SHELL_CHARS])
        try:
            toks = shlex.split(cmd, posix=True)
        except ValueError:
            raise _vr("引号不配对", "unbalanced quotes") from None
        bad = [c for t in toks if t != "&&" and t not in _DISCARD for c in t if c in _SHELL_CHARS]
        if bad or any(not t for t in toks):
            quotes = [c for c in cmd if c in "'\""] if any(" " in t for t in toks) else []
            raise _meta_refusal(quotes + bad if bad else ["'"])
        toks = [None if t == "&&" else t for t in toks if t not in _DISCARD]
    cmds, cur = [], []
    for t in toks:
        if t is None:
            if not cur:
                raise _vr("有空命令", "has an empty command")
            cmds.append(cur)
            cur = []
            continue
        cur.append(t)
    if not cur:
        raise _vr("有空命令", "has an empty command")
    cmds.append(cur)
    if len(cmds) > 4 or any(len(c) > 32 for c in cmds):
        raise _vr("太复杂（最多 4 条命令、每条 32 个参数）", "is too complex (4 commands, 32 arguments each)")
    for argv in cmds:
        prog = argv[0]
        base = prog.rsplit("/", 1)[-1]
        if base in _NO_RUN:
            raise _vr(f"要运行 {base}（自检不运行它）", f"runs {base} (never run by a self-test)")
        code = -1                                    # index of the inline-code argument (certified only)
        if _INLINE.fullmatch(base):
            for i, a in enumerate(argv[1:-1], 1):
                if a == "-m" and argv[i + 1] in _NO_MODULE:
                    raise _vr(f"要运行 -m {argv[i + 1]}（自检不运行它）", f"runs -m {argv[i + 1]} (never run by a self-test)")
            for i, a in enumerate(argv[1:], 1):
                if not a.startswith("-"):
                    break                      # the script / module: what follows are its own arguments
                inline = a in ("-e", "-E", "--eval", "-p", "--print") or (a.startswith("-c") and not a.startswith("--")) or \
                    (a.startswith("-") and not a.startswith("--") and "c" in a[1:] and base.startswith("python"))
                if inline:
                    if not certified:
                        raise _vr("用了内联代码", "uses inline code", f" {clean_line(a, 8)}（只接受包里的脚本 / "   # round 1 (L4):
                                  "package scripts only）")                                              # short = fixed words
                    attached = a not in ("-e", "-E", "--eval", "-p", "--print") and not a.endswith("c")
                    code = i if attached else i + 1
                    break
                if a == "-m":
                    break
        for i, a in enumerate(argv):
            if i != code and (a.startswith("/") or a == ".." or a.startswith("../") or "/../" in a or a.endswith("/..")):
                raise _vr("只能用包内相对路径", "may use package-relative paths only", f"（{clean_line(a, 60)}）")
    return cmds


# ------------------------------------------------------------------ the verify sandbox
# install.verify runs package code. It runs ONLY inside a real sandbox (Linux: bubblewrap, macOS: sandbox-exec) that sees
# the system read-only, not the user's home / agentj's state / /tmp, has no network and can write only into the throw-away
# copy; without one it is skipped and the Owner is told so (NO_SANDBOX). The command grammar (parse_verify) is a second layer.
VERIFY_BASE = "/usr/local/bin:/usr/bin:/bin"
# ADR-0269-w4-plaza (#478/#466): Homebrew's bin folders (Apple silicon /opt/homebrew, Linuxbrew; Intel Homebrew is /usr/local)
# join the self-check PATH — after the system folders, read-only inside the sandbox (bwrap --ro-bind / ; sandbox-exec denies
# every write but the scratch copy). Only these fixed folders, never the user's PATH: real folders outside every home, owned by
# root or this user, not writable by group / others.
BREW_BINS = ("/opt/homebrew/bin", "/home/linuxbrew/.linuxbrew/bin")


def brew_problem(d: str) -> str | None:
    try:
        r = os.path.realpath(d)
        st = os.stat(r)
    except OSError:
        return "directory unavailable / 目录不可读"
    if not stat.S_ISDIR(st.st_mode):
        return "not a directory / 不是目录"
    if st.st_uid not in (0, os.getuid()):
        return "directory owner is neither root nor this user / 目录属主不是 root 或当前用户"
    if st.st_mode & 0o022:
        return f"group/other write permission / 组或其他人可写 (mode {stat.S_IMODE(st.st_mode):04o})"
    if any(fence._under(r, os.path.realpath(h)) for h in _homes()):
        return "real path is inside a home / 真实路径在用户目录内"
    return None


def _brew_ok(d: str) -> bool:
    return brew_problem(d) is None


def verify_path() -> str:
    return ":".join([VERIFY_BASE, *(d for d in BREW_BINS if _brew_ok(d))])


SANDBOX_EXEC = fence.SANDBOX_EXEC
SANDBOX_TEXT = {"bubblewrap": "bubblewrap 沙箱 sandbox", "sandbox-exec": "macOS sandbox-exec 沙箱 sandbox"}
# what python (and the usual self-test tools) read on macOS; outside any home, listed so a deny on the home cannot hide them
_MAC_SYSTEM = ("/usr", "/bin", "/sbin", "/System", "/Library", "/private/etc", "/private/var/db", "/opt/homebrew", "/dev")
_SANDBOX: dict = {}


def _homes() -> list[str]:
    """$HOME and the home directory of this OS user (they differ under a test or a `sudo -E`)."""
    out = [os.path.expanduser("~")]
    try:
        import pwd
        out.append(pwd.getpwuid(os.getuid()).pw_dir)
    except (ImportError, KeyError, OSError):
        pass
    return [h for h in out if h]


VERIFY_PATH = verify_path()


def verify_env(scratch) -> dict:
    s = os.path.realpath(scratch)
    return {"PATH": VERIFY_PATH, "HOME": os.path.join(s, "home"), "TMPDIR": os.path.join(s, "tmp"), "LANG": "C.UTF-8"}


def _hidden_dirs(paths) -> list[str]:
    """Real, existing directories (never "/"), a directory inside another one dropped (its parent hides it)."""
    cand = sorted({os.path.realpath(p) for p in paths if p and os.path.isdir(p)} - {"/"}, key=lambda p: (len(p), p))
    out: list[str] = []
    for c in cand:
        if not any(fence._under(c, o) for o in out):
            out.append(c)
    return out


def verify_bwrap_argv(scratch, pkg_root, *, hide=(), homes=None, bwrap=None) -> list[str]:
    """The strict bubblewrap prefix for install.verify (ends with "--"): the whole system read-only; an empty tmpfs over
    $HOME, the real home, agentj's state (`hide`), /tmp, /run, /var/tmp; then the scratch folder bound back read-write on
    top (it may live under /tmp); a fresh /dev and /proc; every namespace unshared (network included); killed with agentj;
    a new session (no terminal injection); an empty environment but PATH / HOME / TMPDIR / LANG; cwd = the package copy."""
    s = os.path.realpath(scratch)
    a = [bwrap or shutil.which("bwrap") or "bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc"]
    for d in _hidden_dirs([*(homes if homes is not None else _homes()), *hide, "/tmp", "/run", "/var/tmp"]):
        a += ["--tmpfs", d]
    a += ["--bind", s, s, "--unshare-all", "--die-with-parent", "--new-session", "--clearenv"]
    for k, v in verify_env(s).items():
        a += ["--setenv", k, v]
    return a + ["--chdir", os.path.realpath(pkg_root), "--"]


def verify_sbpl_profile(scratch, *, hide=(), homes=None) -> tuple[str, dict]:
    """(profile, -D parameters) for `sandbox-exec` (macOS). Paths travel as parameters, never spliced into the profile (as in
    fence.sbpl_profile). Later rules win: no network; no write anywhere but the scratch folder (and /dev/null & co.); the
    homes and agentj's state unreadable, the scratch folder and the system paths python needs readable again."""
    params = {"SCRATCH": os.path.realpath(scratch)}
    deny = list(dict.fromkeys(os.path.realpath(p) for p in [*(homes if homes is not None else _homes()), *hide] if p))
    deny = [p for p in deny if p != "/"]
    for i, p in enumerate(deny):
        params[f"HIDE_{i}"] = p
    q = lambda p: '"' + p.replace("\\", "\\\\").replace('"', '\\"') + '"'  # noqa: E731  (constants only)
    lines = [
        "(version 1)",
        "(allow default)",
        ";; install.verify (agentj plaza install): no network, no writes outside the throw-away copy, the home unreadable",
        "(deny network*)",
        '(deny file-write* (subpath "/"))',
        '(allow file-write* (subpath (param "SCRATCH")) (literal "/dev/null") (literal "/dev/zero") (literal "/dev/dtracehelper")'
        ' (regex #"^/dev/fd/[0-9]+$"))',
        *([] if not deny else ["(deny file-read*", *[f'  (subpath (param "HIDE_{i}"))' for i in range(len(deny))], ")"]),
        '(allow file-read* (subpath (param "SCRATCH")))',
        "(allow file-read* " + " ".join(f"(subpath {q(p)})" for p in _MAC_SYSTEM) + ")",
        "(allow file-read-metadata (literal \"/\")" + "".join(f' (literal (param "HIDE_{i}"))' for i in range(len(deny))) + ")",
        "(deny signal)",
        "(allow signal (target same-sandbox))",
        "(deny process-info*)",
        "(allow process-info* (target same-sandbox))",
        "(deny job-creation)",
        "(deny appleevent-send)",
        "(deny lsopen)",
    ]
    return "\n".join(lines) + "\n", params


def verify_sandbox_argv(kind: str, scratch, pkg_root, *, hide=()) -> list[str]:
    if kind == "bubblewrap":
        return verify_bwrap_argv(scratch, pkg_root, hide=hide)
    if kind == "sandbox-exec":
        prof, params = verify_sbpl_profile(scratch, hide=hide)
        a = [SANDBOX_EXEC, "-p", prof]
        for k, v in params.items():
            a += ["-D", f"{k}={v}"]
        return a
    raise ValueError(kind)


def _probe_sandbox() -> str | None:
    """The sandbox this machine can really start, probed once: Linux bubblewrap (as fence.problem detects it: on PATH, and
    it must be able to create the namespaces), macOS /usr/bin/sandbox-exec (and the home must be unreadable from inside)."""
    if sys.platform == "linux":
        kind = "bubblewrap" if shutil.which("bwrap") else None
    elif sys.platform == "darwin":
        kind = "sandbox-exec" if os.access(SANDBOX_EXEC, os.X_OK) else None
    else:
        kind = None
    if not kind:
        return None
    true = shutil.which("true", path=VERIFY_PATH)
    ls = shutil.which("ls", path=VERIFY_PATH)
    if not true or not ls:
        return None
    try:
        with tempfile.TemporaryDirectory(prefix="agentj-verify-probe-") as tmp:
            scratch = pathlib.Path(tmp)
            for d in ("pkg", "home", "tmp"):
                (scratch / d).mkdir(mode=0o700)
            pre = verify_sandbox_argv(kind, scratch, scratch / "pkg")
            run = lambda argv: subprocess.run(pre + argv, cwd=scratch / "pkg", env=verify_env(scratch),  # noqa: E731
                                              stdin=subprocess.DEVNULL, capture_output=True, timeout=10)
            if run([true]).returncode != 0:
                return None
            if kind == "sandbox-exec" and run([ls, os.path.realpath(os.path.expanduser("~"))]).returncode == 0:
                return None              # the profile did not apply (already inside another sandbox?)
    except (OSError, subprocess.SubprocessError):
        return None
    return kind


def sandbox_kind() -> str | None:
    """"bubblewrap" / "sandbox-exec" when install.verify can run in a real sandbox here, else None (cached per process)."""
    if "kind" not in _SANDBOX:
        _SANDBOX["kind"] = _probe_sandbox()
    return _SANDBOX["kind"]


def min_python(m) -> tuple[int, int] | None:
    """#478: the lowest Python a package declares — `requires.cli` python3 / python `version` like ">=3.12", "3.12+", "3.12"."""
    for c in summarize(m)["cli"]:
        hit = c["name"] in ("python3", "python") and re.fullmatch(r"\s*(?:>=|≥|~=|\^)?\s*3\.(\d{1,2})(?:\.\d+)?\s*\+?\s*", c["version"] or "")
        if hit:
            return 3, int(hit.group(1))
    return None


def pick_python(want, prefix, env, cwd, deadline=None) -> str | None:
    """#478: the first python3 / python3.N on the self-check PATH whose version (asked inside the same sandbox) is ≥ `want`.
    Security r1 L3: each probe ≤ min(10 s, what is left of the self-check `deadline`); a spent deadline probes nothing."""
    seen = []
    for n in ["python3", *(f"python3.{x}" for x in range(20, want[1] - 1, -1))]:
        exe = shutil.which(n, path=VERIFY_PATH)
        if not exe or os.path.realpath(exe) in seen:
            continue
        seen.append(os.path.realpath(exe))
        left = 10.0 if deadline is None else min(10.0, deadline - _now_mono())
        if left <= 0:
            return None
        try:
            r = subprocess.run(prefix + [exe, "-I", "-S", "-c", "import sys;print(*sys.version_info[:2])"], cwd=cwd, env=env,
                               stdin=subprocess.DEVNULL, capture_output=True, timeout=left, start_new_session=True)
            if r.returncode == 0 and tuple(map(int, r.stdout.split())) >= want:
                return exe
        except (OSError, ValueError, subprocess.SubprocessError):
            continue
    return None


def run_verify(cmd: str, pkg_root: pathlib.Path, scratch: pathlib.Path, timeout: float | None = None, *, kind: str,
               hide=(), certified: bool = False, python=None) -> tuple[int, str]:
    """Run `install.verify` in `pkg_root` (inside `scratch`) in the `kind` sandbox, with PATH + a temporary HOME / TMPDIR +
    LANG and nothing else. → (exit code, last output). Timeout → (124, …). There is no unsandboxed path."""
    if kind not in SANDBOX_TEXT:
        raise ValueError("run_verify needs a sandbox")
    cmds = parse_verify(cmd, certified=certified)
    env = verify_env(scratch)
    for d in ("home", "tmp"):
        os.makedirs(scratch / d, mode=0o700, exist_ok=True)
    prefix = verify_sandbox_argv(kind, scratch, pkg_root, hide=hide)
    deadline = _now_mono() + (VERIFY_TIMEOUT if timeout is None else timeout)
    tail = ""
    for argv in cmds:
        prog = argv[0]
        if "/" in prog:
            exe = str(pkg_root / prog)
            if not os.path.isfile(exe):
                return 127, f"{prog}: not in the package"
        elif python and prog in ("python3", "python"):
            exe = pick_python(python, prefix, env, pkg_root, deadline)
            if not exe and deadline - _now_mono() <= 0:
                return 124, "timeout"
            if not exe:
                return 127, f"python3.{python[1]}: not found on PATH (needs Python ≥ {python[0]}.{python[1]})"
        else:
            exe = shutil.which(prog, path=VERIFY_PATH)
            if not exe:
                return 127, f"{prog}: not found on PATH"
        left = deadline - _now_mono()
        if left <= 0:
            return 124, "timeout"
        try:
            p = subprocess.Popen(prefix + [exe, *argv[1:]], cwd=pkg_root, env=env, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
        except OSError as e:
            return 126, f"{prog}: cannot run ({e.strerror})"
        try:
            o, _ = p.communicate(timeout=left)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except OSError:
                pass
            p.communicate()
            return 124, f"timeout ({VERIFY_TIMEOUT if timeout is None else timeout} s)"
        tail = o[-4000:].decode("utf-8", "replace")
        if p.returncode != 0:
            return p.returncode, tail
    return 0, tail


def _now_mono() -> float:
    import time
    return time.monotonic()


# ------------------------------------------------------------------ install
def resolve_harness(arg: str | None, st) -> str:
    if arg:
        if arg not in HARNESSES:
            raise PlazaError("--harness: claude_code | codex | opencode")
        return arg
    a = st.agent_config()
    if a:
        # P305: Kimi Code reads ~/.agents/skills — the Codex target — so a package for Codex is the one it gets
        return {"claude": "claude_code", "codex": "codex", "opencode": "opencode", "kimi": "codex", "qwen": "codex", "dsh": "codex"}[a["kind"]]
    from . import harness as hz
    d = hz.detect()["decision"]
    if d.startswith("use:"):
        return {"claude": "claude_code", "codex": "codex", "opencode": "opencode", "kimi": "codex", "qwen": "codex", "dsh": "codex"}[d[4:]]
    raise PlazaError("装给哪个 Agent？问你的人，然后加 --harness claude_code | codex | opencode / which Agent? ask your human, "
                     "then add --harness")


def skills_root(harness: str) -> pathlib.Path:
    home = pathlib.Path(os.path.expanduser("~"))
    if harness == "claude_code":
        return home / ".claude" / "skills"
    if harness == "codex":
        return home / ".agents" / "skills"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return (pathlib.Path(xdg) if xdg and os.path.isabs(xdg) else home / ".config") / "opencode" / "skills"


def workspace_root(arg: str | None) -> pathlib.Path:
    w = arg or os.environ.get("AGENT_WORKSPACE") or "~/agent-workspace"
    return pathlib.Path(os.path.abspath(os.path.expanduser(w)))


def target_of(manifest: dict, harness: str, workspace: str | None) -> pathlib.Path:
    """Only from regex-checked names: <skills root>/<skill_dir_name> or <workspace>/<name>."""
    if manifest["type"] == "skill":
        d = manifest["install"]["skill_dir_name"]
        if not _NAME.fullmatch(d):
            raise PlazaError("bad skill_dir_name")
        return skills_root(harness) / d
    if not _NAME.fullmatch(manifest["name"]):
        raise PlazaError("bad name")
    return workspace_root(workspace) / manifest["name"]


def install_digest(sha: str, target: pathlib.Path, harness: str, params: dict, flags: dict) -> str:
    blob = json.dumps({"sha256": sha, "target": str(target), "harness": harness, "params": params, "flags": flags},
                      ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _cache_get(st, sha: str) -> bytes | None:
    p = st.root / "plaza" / "cache" / f"{sha}.ajpkg"
    try:
        data = p.read_bytes()
    except OSError:
        return None
    return data if bundle.sha256(data) == sha else None


def _cache_put(st, sha: str, data: bytes) -> None:
    d = _plaza_dir(st) / "cache"
    d.mkdir(mode=0o700, exist_ok=True)
    st.write_private(d / f"{sha}.ajpkg", data)
    seen = []
    for p in d.glob("*.ajpkg"):  # best effort: a parallel install may delete p first
        try:
            seen.append((p.stat().st_mtime, p))
        except OSError:
            pass
    for _, p in sorted(seen)[:-CACHE_KEEP]:
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass


def fetch(st, name: str, version: str | None, *, post=None, get=None) -> tuple[dict, bytes, dict, dict]:
    """get → download (or the cache) → SHA-256 = the server's → bundle.parse → same name / type / version.
    → (detail, bundle bytes, manifest, files)."""
    d, dl = _get(st, name, version, post)
    # contract §2: for a package listed as official / certified, any integrity failure is a signature failure (exit 5)
    integrity = SignatureError if (d.get("certified") or d.get("track") == "official") else PlazaError
    sha = d["sha256"]
    if not sha:
        raise PlazaError("服务器没给包的 SHA-256 / the server sent no SHA-256")
    data = _cache_get(st, sha)
    if data is None:
        try:
            status, data = cloud.plaza_download(st, dl.get("url"), **({"get": get} if get else {}))
        except cloud.CloudError as e:
            raise PlazaError("下载地址不是本机配置的 API / the download URL is not on the configured API" if e.kind == "refused_url"
                             else f"下载失败 / download failed ({e.kind})")
        if status != 200:
            raise PlazaError(f"下载失败 / download failed (HTTP {status})")
        if bundle.sha256(data) != sha or (d["bytes"] is not None and len(data) != d["bytes"]):
            raise integrity("下载的包和服务器说的 SHA-256 / 大小不一致：拒绝 / the bundle does not match the server's SHA-256: refused")
        _cache_put(st, sha, data)
    try:
        m, files = bundle.parse(data)
    except bundle.BundleError as e:
        raise integrity(f"包不合格，拒绝 / invalid package: {e}")
    if m["name"] != name or m["type"] != d["type"] or (version and m["version"] != version) or \
            (d["version"] and m["version"] != d["version"]):
        raise integrity("包里的 manifest 和服务器说的名字 / 类型 / 版本不一致：拒绝 / the manifest does not match the listing: refused")
    return d, data, m, files


def check_signature(d: dict, data: bytes, m: dict, keyring: dict | None = None) -> bool:
    """True = 官方认证 (a valid signature by a compiled-in key for exactly this package). A server-flagged official /
    certified package without one, or any package with a signature that does not verify → SignatureError (exit 5)."""
    flagged = d["track"] == "official" or d["certified"]
    if not d["signature"]:
        if flagged:
            raise SignatureError("服务器说这是认证包，但没有签名：拒绝安装 / the server says certified but there is no signature: refused")
        return False
    try:
        minisign.verify_package(data, d["signature"], m, keyring)
    except minisign.MinisignError as e:
        raise SignatureError(f"签名无效，拒绝安装（包可能被篡改）/ invalid signature, refused (the package may be tampered with): {e}")
    return True


def semver_key(v: str) -> tuple:
    """SemVer 2 precedence: a pre-release is lower than its release; numeric identifiers < alphanumeric ones."""
    m = bundle.SEMVER.fullmatch(v)
    if not m:
        raise ValueError(v)
    core = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    if not m.group(4):
        return (*core, 1, ())
    ids = tuple((0, int(x), "") if x.isdigit() else (1, 0, x) for x in m.group(4)[1:].split("."))
    return (*core, 0, ids)


def trust_check(st, m: dict, certified: bool, version_arg: str | None) -> str | None:
    """What a compromised server could try beyond a bad signature (contract §10): an official-looking name without the
    official signature, an unsigned copy of a package this machine installed signed, or an older version. → a downgrade note
    for the preview (only with an explicit --version), or None. Raises SignatureError (exit 5) / PlazaError."""
    names = [m["name"]] + ([m["install"]["skill_dir_name"]] if m["type"] == "skill" else [])
    if not certified and any(RESERVED.match(n) for n in names):
        raise SignatureError(f"「{'」/「'.join(names)}」是保留给官方的名字，但这个包没有有效的官方签名：拒绝安装 / a name reserved "
                             "for official packages, without a valid official signature: refused")
    recs = [r for r in read_installed(st) if r["name"] == m["name"]]
    if not certified and any(r.get("certified") is True for r in recs):
        raise SignatureError(f"本机装的 {m['name']} 是官方签名版，这个包没有有效签名：拒绝（服务器可能被篡改）/ this machine has the "
                             "officially signed copy of this package; this one carries no valid signature: refused (the server "
                             "may be compromised)")
    vers = [r["version"] for r in recs if isinstance(r.get("version"), str) and bundle.SEMVER.fullmatch(r["version"])]
    if vers:
        top = max(vers, key=semver_key)
        if semver_key(m["version"]) < semver_key(top):
            if not version_arg:
                raise PlazaError(f"服务器给的 {m['name']} {m['version']} 比本机已装的 {top} 旧：拒绝（降级只在你明确 --version "
                                 f"{m['version']} 时才装）/ the server offers {m['version']}, older than the installed {top}: "
                                 f"refused (a downgrade needs an explicit --version {m['version']})")
            return (f"降级 DOWNGRADE：本机已装 {top}，这次装更旧的 {m['version']}（你用 --version 明确指定的）/ this installs an "
                    f"OLDER version than the installed {top} (explicit --version)")
    return None


def _owned(st, target: pathlib.Path, name: str) -> bool:
    """installed.json says agentj installed `target` for this package name (the only case --replace may move it)."""
    return any(r["name"] == name and str(target) in (r["target"], os.path.realpath(r["target"])) for r in read_installed(st))


NOT_OURS = "这个目录不是这个包装的，不替换 / this folder was not installed by agentj for this package: not replaced"


def _backup_path(st, name: str) -> pathlib.Path:
    """<state dir>/plaza/backups/<name>-<UTC ts>[-n]/ (0700): never inside a skills root or the workspace."""
    d = _plaza_dir(st) / "backups"
    d.mkdir(mode=0o700, exist_ok=True)
    os.chmod(d, 0o700)
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bak, i = d / f"{name}-{ts}", 1
    while os.path.lexists(bak):
        bak, i = d / f"{name}-{ts}-{i}", i + 1
    return bak


def run_install(name: str, *, version=None, harness=None, workspace=None, params=None, params_file=None, accept_unverified=False,
                sign_as=None, replace=False, skip_verify=False, owner_confirmed=False, digest=None, st=None, post=None, get=None,
                keyring=None, now=None, out=None, _starter=False, perm_call=None, host_ask=None) -> int:
    """host_ask (#428, serve only — plaza_host): the Agent J host runs this install. No preview step: ONE signed card
    (host_ask(info) → {ok, rid, device} | {ok: False, why}) stands for the owner's yes to exactly this package, sha256 and
    digest — including an unverified community package; every check below stays as it is."""
    out = out or sys.stdout
    if host_ask is not None and _starter:
        raise PlazaError("starter installs are not host requests")
    if owner_confirmed and not (digest and _DIGEST.fullmatch(digest)):
        raise PlazaError("--owner-confirmed 要和预览时打印的 --digest 一起用：先不带它运行一次，把预览给你的人看 / "
                         "--owner-confirmed needs the --digest printed by the preview")
    if sign_as is not None and not (_SIGN_AS.fullmatch(sign_as) and _value_problem(sign_as) is None):
        raise PlazaError("--sign-as: 1–40 个字（字母、数字、空格、. · -）/ 1–40 letters, digits, spaces, . · -")
    st = st or _state()
    if not cloud.read_cloud(st):
        raise PlazaError(ERRORS["unlinked"])
    try:
        d, data, m, files = fetch(st, name, version, post=post, get=get)
        sha = bundle.sha256(data)
        certified = check_signature(d, data, m, keyring)
        downgrade = trust_check(st, m, certified, version)
        pubr = market_guard.publisher(st, d, sha, m, d["track"] == "official")     # P426 #181-2: the publisher's own signature
    except (SignatureError, market_guard.PublisherMismatch) as e:
        print(f"refused: {e}", file=out)
        return EXIT_SIGNATURE
    findings = plaza_audit.audit(files, m)                                          # P426 #181-2: the static audit
    # Jarvis ruling 3: an official signature does not exempt a block finding — refused, except that the owner may allow an
    # officially signed package once with a signed phone card listing every finding (below, at confirm; never a starter)
    if plaza_audit.blocked(findings) and (not certified or _starter):
        print(market_guard.refusal(findings, "没有安装"), file=out)
        st.log("plaza_pkg_install", name=m["name"], result="audit_blocked")
        return EXIT_BLOCKED
    audit_blk = market_guard.audit_items(findings) if certified else []

    if m["type"] == "card":          # P432: a card is not for an AI coding tool — it goes onto the phone, switched off
        if _starter or params or params_file or sign_as or workspace:
            raise PlazaError("卡片没有参数、签名人和工作目录 / a card takes no --param, --sign-as or --workspace")
        return _install_card(st, m, files, sha, certified, downgrade, name=name, version=version, replace=replace,
                             accept_unverified=accept_unverified, owner_confirmed=owner_confirmed, digest=digest, post=post, out=out,
                             pubr=pubr, findings=findings, audit_blk=audit_blk, perm_call=perm_call, host_ask=host_ask)
    h = resolve_harness(harness, st)
    if _starter:
        from .starters import STARTERS
        if (name not in STARTERS or m['type'] != 'workflow' or not certified or version is not None
                or replace or skip_verify or accept_unverified or sign_as or params or params_file
                or any(g.get('required') for g in m.get('credentials', []))):
            raise PlazaError('automatic starter must be a curated signed workflow with account-free defaults')
        # Install/upgrade's curated examples are pre-authorized; all ordinary marketplace installs
        # retain the preview and owner-confirmation contract. Never sign on the owner's behalf.
    s = summarize(m)
    if s["harness"] and h not in s["harness"]:
        raise PlazaError(f"这个包只支持 / the package supports only: {', '.join(s['harness'])}")
    target, pin = market_guard.pin_target(target_of(m, h, workspace))    # #428 H1: the real path, on card / digest / record
    if host_ask is not None and (why := fence.host_write_refusal(st, str(target))):
        raise PlazaError(f"不装到 {target}：那是 Agent J 受保护的位置（{why}），Agent 自己也写不了 / refused: a protected location")
    now = now or datetime.datetime.now().astimezone()
    values, auto = {}, set()
    if m["type"] == "workflow":
        schema = json.loads(files["params.schema.json"].decode("utf-8"))
        values, auto = resolve_params(schema, params or [], params_file, h, now)
    elif params or params_file:
        raise PlazaError("技能没有参数 / a skill takes no --param")
    shown_params = {k: v for k, v in values.items() if k not in auto}
    flags = {"replace": bool(replace), "sign_as": sign_as, "skip_verify": bool(skip_verify), "publisher": pubr["fp"]}
    dg = install_digest(sha, target, h, shown_params, flags)
    install_date = str(values.get("install_date") or now.date().isoformat())
    if not _DATE.fullmatch(install_date):
        install_date = now.date().isoformat()
    # render now (preview too): a placeholder / JSON problem is found before the human is asked
    rendered = install_files(m, files, h, values, sign_as, install_date)
    # P426 #181-1: declared ∪ inferred permissions (S1) vs what this harness already allows + what the owner approved before
    try:
        perm, perm_inferred, perm_under = plaza_perms.effective(m, files)
    except plaza_perms.PermError as e:
        raise PlazaError(f"包的权限声明不合格，不装 / the package's permissions declaration is malformed: {e}")
    perm_allow, perm_src = plaza_perms.allowed(h)
    perm_prev = plaza_perms.merge(*[r.get("permissions") for r in read_installed(st)
                                    if r["name"] == m["name"] and r["harness"] == h and isinstance(r.get("permissions"), dict)])
    perm_extra = plaza_perms.excess(perm, perm_allow, perm_prev)
    if _starter or host_ask is not None:
        owner_confirmed, digest = True, dg
    exists = os.path.lexists(target)
    owned = exists and _owned(st, target, m["name"])
    if exists and replace and not owned:
        raise PlazaError(f"{target}: {NOT_OURS}")
    sandbox = None if (skip_verify or not s["verify"]) else sandbox_kind()
    vref, vcmds = None, []            # #437: the grammar is judged BEFORE any card; refused → the self-test is skipped
    if s["verify"] and not skip_verify:
        try:
            vcmds = parse_verify(m["install"]["verify"], certified=certified)
            _verify_files_shipped(m, files)
        except VerifyRefused as e:
            vref, sandbox = e, None
    deps = plaza_deps.missing(plaza_deps.wanted(s, vcmds), VERIFY_PATH)     # #466: known before the card; never a blocker

    if not owner_confirmed:
        out_lines = [FENCE_OPEN, f"{META}{NOTE}",
                     f"{META}安装预览 install preview: {m['name']} · {_kind(m['type'])} · v{m['version']} · "
                     + (CERT_BADGE if certified else UNVERIFIED),
                     f"{META}包 bundle sha256 {sha} · {len(files)} 个文件 files · {sum(len(v) for v in files.values())} bytes",
                     *([f"{META}{downgrade}"] if downgrade else []),
                     *market_guard.publisher_lines(pubr, d["track"] == "official"),
                     *market_guard.audit_lines(findings, certified),
                     *_perm_lines(perm, perm_inferred, perm_allow, perm_src, perm_extra, perm_under),
                     f"{META}标题 title:", *_t(m["title_zh"]), *_t(m["title_en"]), f"{META}简介 summary:", *_t(m["summary_zh"]),
                     *_t(m["summary_en"]), *_summary_lines(s)]
        if s["verify"]:
            out_lines.append(
                f"{META}install.verify：你选了 --skip-verify，确认安装时不运行 / skipped by --skip-verify: it will not run"
                if skip_verify else
                f"{META}install.verify：{VERIFY_SKIPPED[0].format(vref.short[0])}（确认安装时不运行，安装继续）/ "
                f"{VERIFY_SKIPPED[1].format(vref.short[1])} (not run at confirm; the install goes on). {vref}"
                if vref else
                f"{META}install.verify：确认安装时在{SANDBOX_TEXT[sandbox]}里运行（无网络、看不到家目录、只能写临时副本）/ runs at "
                f"confirm inside the {sandbox} sandbox (no network, home hidden, writes only to the throw-away copy)"
                if sandbox else
                f"{META}install.verify：{NO_SANDBOX}（确认安装时也不会运行）/ no usable sandbox on this machine: the package's "
                "self-test is NOT run (not at confirm either)")
        if deps:
            out_lines.append(_deps_line(deps))
        out_lines += [f"{META}装到 target ({h}): {target}" + ("（已存在 EXISTS）" if exists else ""),
                      f"{META}将写入的文件 files to write（{len(rendered)} 个，装到 target 下的相对路径 / paths under the target）:",
                      *_t("\n".join(sorted(rendered, key=lambda x: x.encode())))]
        if m["type"] == "workflow":
            props = schema.get("properties", {})
            out_lines.append(f"{META}参数 params（{len(values)}；x-auto 由 agentj 计算 / computed by agentj）:")
            for k in sorted(values, key=lambda k: (props.get(k, {}).get("x-ask-order", 999) if isinstance(props.get(k), dict) else 999, k)):
                spec = props.get(k) if isinstance(props.get(k), dict) else {}
                out_lines.append(f"{META}  · {k}" + (" (x-auto)" if k in auto else "") + (" (必填 required)" if k in schema.get("required", []) else "") + ":")
                out_lines += _t(f"= {_as_text(values[k])}" + (f"  — {_s(spec.get('title'), 120)}" if spec.get("title") else ""))
            signed = sorted(p for p, b in files.items() if p.startswith("scaffold/") and _VERIFIED.search(b.decode("utf-8", "replace")))
            if signed:
                out_lines.append(f"{META}签字 signature: " + (f"以下 {len(signed)} 份文档将以「{escape_data(sign_as)}」的名义签字生效 / these "
                                 f"{len(signed)} documents will be signed in the name of {escape_data(sign_as)!r}:" if sign_as else
                                 f"不签字：{len(signed)} 份文档装成 verified: []，第一次 /start-session 时逐份签 / not signed "
                                 f"(--sign-as NAME to sign): {len(signed)} documents get verified: []"))
                out_lines += _t("\n".join(p[len("scaffold/"):] for p in signed))
            if h == "codex":
                n = sum(1 for p in rendered if p.startswith(".codex/agents/"))
                out_lines.append(f"{META}Codex: 另生成 {n} 个 .codex/agents/<role>.toml / also generates {n} role files")
        out_lines.append(FENCE_CLOSE)
        print("\n".join(out_lines), file=out)
        if exists and not replace:
            print(f"注意 / note: {target} 已存在；确认时会拒绝。" + (
                  f"它是 agentj 为这个包装的：加 --replace 会把旧目录移到 {st.root / 'plaza' / 'backups'}/ / it was installed by "
                  "agentj for this package: --replace moves it to the backups folder of agentj's state" if owned else
                  f"{NOT_OURS}（--replace 也不行；改名或移走它由你的人决定）"), file=out)
        if not certified:
            print("这是待审核的社群包：没有 Agent J 官方签名，内容由别的客户写。确认时必须加 --accept-unverified / an UNVERIFIED community "
                  "package written by another customer: the confirm needs --accept-unverified", file=out)
        print(f"digest: {dg}", file=out)
        if m["type"] == "workflow":
            print(WS_DIGEST, file=out)
        print(_next_step(name, version=version, harness=h, workspace=workspace, params=params, params_file=params_file,
                         sign_as=sign_as, replace=replace), file=out)
        flags = ("--skip-verify " if skip_verify else "") + ("" if certified else "--accept-unverified ") + f"--owner-confirmed --digest {dg}"
        print(f"（主人在终端自己确认也可：同一条 agentj plaza install 加 {flags} / the owner at a terminal: the same install + {flags}）",
              file=out)
        return EXIT_OK

    if digest != dg:
        print("refused: this is not the install your human confirmed (digest mismatch — the package, version, target, harness, "
              "params or options changed; for a workflow often --workspace or a --param: the digest includes both). Run the "
              "preview again and show your human the new one. / digest 不符：包、版本、装到哪（工作流的 --workspace）、参数或选项变了，"
              "重新预览拿新 digest", file=out)
        return EXIT_DIGEST
    if not certified and not accept_unverified and host_ask is None:     # #428: the host card says it and stands for it
        raise PlazaError("待审核的社群包要加 --accept-unverified（你的人看过并同意了）/ an unverified community package needs "
                         "--accept-unverified (your human saw it and agreed)")
    if exists and not replace:
        raise PlazaError(f"{target} 已存在，不覆盖 / exists, never overwritten" + (
            "；要替换加 --replace / add --replace to move it to the backups" if owned else f"; {NOT_OURS}"))

    # 0. P426 #181-1: more than the Agent may do, and/or (N4) a changed publisher → ONE signed card on the phone (no serve →
    #    refused, with the way); no command-line flag replaces it
    grant = pub_grant = audit_grant = None
    pub_changed = pubr["changed"] and not _starter
    if host_ask is not None:
        res = host_ask({"name": m["name"], "version": m["version"], "type": m["type"], "sha256": sha, "digest": dg,
                        "certified": certified, "publisher": pubr, "perm": perm, "extra": perm_extra, "under": bool(perm_under),
                        "audit": [{k: f[k] for k in ("rule", "path", "line", "level")} for f in findings[:30]],
                        "target": str(target), "harness": h, "replace": bool(exists and replace),
                        "verify": ("skip" if skip_verify else "refused" if vref else sandbox or "none") if s["verify"] else None,
                        "verify_why": vref.short if vref else None, "os_warning": os_warning(s["os"]), "deps": [x["name"] for x in deps],
                        "params": {k: _as_text(v) for k, v in shown_params.items()}, "sign_as": sign_as,
                        "downgrade": downgrade, "files": sorted(rendered, key=lambda x: x.encode())})
        if not res.get("ok"):
            print(_perm_refusal(name, res, bool(perm_extra), pub_changed, bool(audit_blk)), file=out)
            st.log("plaza_pkg_install", name=m["name"], result="host_" + str(res.get("why"))[:20])
            return EXIT_PERMISSION
        g = {"rid": res.get("rid"), "device": res.get("device")}
        grant, pub_grant = (g if perm_extra else None), (g if pub_changed else None)
        audit_grant = {**g, "findings": [a["rule"] for a in audit_blk]} if audit_blk else None
        print("主人已在手机上签名允许这次安装 / the owner allowed this install on the phone (signed)", file=out)
        if m["type"] == "workflow":
            print(WS_DIGEST, file=out)
    elif (perm_extra and not _starter) or pub_changed or audit_blk:
        items = perm_extra if not _starter else []
        under = bool(perm_under) and any(i in perm_under for i in items)
        res = plaza_perms.ask_owner(st, m["name"], m["version"], sha, items, call=perm_call, under=under,
                                    publisher={"prev": pubr["prev"], "new": pubr["fp"]} if pub_changed else None,
                                    audit=audit_blk or None)
        if res is None or not res.get("ok"):
            print(_perm_refusal(name, res, bool(items), pub_changed, bool(audit_blk)), file=out)
            st.log("plaza_pkg_install", name=m["name"], result=("permission_" if items else "publisher_" if pub_changed else "audit_")
                   + ("no_serve" if res is None else str(res.get("why"))[:20]))
            return EXIT_PERMISSION
        g = {"rid": res.get("rid"), "device": res.get("device")}
        grant = g if items else None
        pub_grant = g if pub_changed else None
        audit_grant = {**g, "findings": [a["rule"] for a in audit_blk]} if audit_blk else None
        what = [x for x in (("权限", "these permissions") if items else None, ("新的发布方", "the new publisher") if pub_changed else None,
                            ("审计拦下的这几处（只这一次）", "the audit findings (this one time)") if audit_blk else None) if x]
        print("和".join(z for z, _ in what) + "：主人已在手机上签名允许 / the owner allowed " + " and ".join(e for _, e in what)
              + " on the phone (signed)", file=out)

    vran = False
    # 1. install.verify — in a throw-away copy of the raw package (before rendering, FORMAT §7.7), only inside a real sandbox
    if s["verify"]:
        if skip_verify:
            print("install.verify: 已按 --skip-verify 跳过，没有运行 / skipped (--skip-verify): not run", file=out)
        elif vref:
            print(f"install.verify: {VERIFY_SKIPPED[0].format(vref.short[0])}，安装继续 / {VERIFY_SKIPPED[1].format(vref.short[1])}, "
                  f"the install goes on. {vref}", file=out)
        else:
            if not sandbox:
                print(f"install.verify: {NO_SANDBOX} / no usable sandbox on this machine: the package's self-test was not run",
                      file=out)
            else:
                rc, tail = _sandboxed(st, m, files, sandbox, certified)
                if _dep_exit(rc, tail):        # #466: "not found" is a dependency to add, not a broken package
                    deps = _add_tail(deps, tail)
                    rc = 0
                    print(f"install.verify: exit 127 — 缺依赖 {', '.join(x['name'] for x in deps)}，安装继续 / missing "
                          "dependencies, the install goes on", file=out)
                elif rc != 0:                  # ADR-0269-w4-plaza (#479): a failing self-test does not block either
                    deps = _add_tail(deps, tail, rc)
                    print(f"install.verify: exit {rc} — 自检没通过，安装继续（{plaza_deps.PENDING[0]}）/ the self-test failed, "
                          f"the install goes on ({plaza_deps.PENDING[1]}). Its output (data):", file=out)
                    print("\n".join([FENCE_OPEN, *_t("\n".join(tail.splitlines()[-20:])), FENCE_CLOSE]), file=out)
                else:
                    print(f"install.verify: OK（{SANDBOX_TEXT[sandbox]}，无网络 / sandboxed, no network）", file=out)
                vran = True

    # 2. staging next to the target, then one rename; a replaced folder goes to <state>/plaza/backups/
    # #428 H1: through the real parent's fd pinned at the card (mkdirat / renameat; a swapped path is refused)
    parent, pfd = target.parent, market_guard.open_pinned(pin)
    staging, old, bak, at = f".{target.name}.agentj-staging-{secrets.token_hex(4)}", None, None, {"src_dir_fd": pfd, "dst_dir_fd": pfd}
    try:
        try:
            write_tree(parent / staging, rendered, dir_fd=pfd)
            try:
                os.lstat(target.name, dir_fd=pfd)
                there = True
            except FileNotFoundError:
                there = False
            if there:                    # round 2 (Info4): a rename race is an error, never a silently wrong `old`
                if not (replace and _owned(st, target, m["name"])):
                    raise PlazaError(f"{target}: {NOT_OURS}" if replace else f"{target} 已存在 / exists")
                old = f".{target.name}.agentj-old-{secrets.token_hex(4)}"
                os.rename(target.name, old, **at)
            try:
                os.rename(staging, target.name, **at)
            except OSError:
                if old is not None:
                    os.rename(old, target.name, **at)
                    old = None
                raise
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True, dir_fd=pfd)
            raise
        if old is not None:
            bak = _backup_path(st, m["name"])
            try:          # round 4: one rename only — never a copy out of a folder the Agent can still change
                os.rename(old, bak, src_dir_fd=pfd)
            except OSError as e:
                bak = parent / old
                print(f"warning: 旧目录没能移到备份处（不在同一个磁盘上时 Agent J 不复制），还在 {bak} / the old folder could not be "
                      f"moved to the backups (no copy across disks) and is still at {bak} ({e.strerror})", file=out)
    finally:
        os.close(pfd)
    rec = {"name": m["name"], "type": m["type"], "version": m["version"], "sha256": sha, "harness": h, "target": str(target),
           "requires": {k: v for k, v in summarize(m).items() if k in ("harness", "os", "cli", "env", "accounts", "python_requirements", "skills")},
           "category": m.get("category"), "certified": certified, "installed_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "permissions": perm, **({"perm_grant": grant} if grant else {}), **({"publisher_grant": pub_grant} if pub_grant else {}),
           **({"audit_grant": audit_grant} if audit_grant else {}),
           "verify": {"state": "pending" if deps else "ok" if vran else "not_run", "missing": [x["name"] for x in deps],
                      "digest": dg, "files": plaza_deps.tree_sha(rendered), "paths": sorted(rendered, key=lambda x: x.encode())}}
    _record_install(st, rec)
    if not pubr["known"] or pub_grant:
        market_guard.record_publisher(st, m["name"], pubr["fp"])    # TOFU: the first publisher seen (unsigned too), or the allowed new one
    st.log("plaza_pkg_install", name=m["name"], result="ok")
    print(f"已安装 / installed: {m['name']} {m['version']} → {target}" + (f"（旧目录 old one → {bak}）" if bak else ""), file=out)
    print("发布方签名 / publisher signature: " + (pubr["short"] or ("Agent J 官方 / official" if d["track"] == "official" else "未签名 unsigned")),
          file=out)
    try:
        _call(st, "installed", {"nonce": _nonce(), "name": m["name"], "version": m["version"]}, post)
    except PlazaError as e:
        print(f"warning: 安装计数没报上去（不影响安装）/ the install count was not reported (the install is fine): {e}", file=out)
    _todo(m, s, target, workspace, st, out)
    _verify_outcome(m["name"], m["version"], deps, vran, out)
    return EXIT_OK


def _sandboxed(st, m, files, sandbox, certified) -> tuple[int, str]:
    with tempfile.TemporaryDirectory(prefix="agentj-verify-") as tmp:
        scratch = pathlib.Path(tmp)
        write_tree(scratch / "pkg", files)
        return run_verify(m["install"]["verify"], scratch / "pkg", scratch, kind=sandbox, hide=[str(st.root)], certified=certified,
                          python=min_python(m))


def _dep_exit(rc: int, tail: str) -> bool:
    return rc == 127 and "not in the package" not in tail


def _add_tail(deps: list, tail: str, rc: int = 127) -> list:
    have = {x["name"] for x in deps}
    deps = deps + [{"name": n, "user": plaza_deps.user_copies(n)} for n in plaza_deps.from_tail(tail) if n not in have]
    return deps or [{"name": f"? (exit {rc})", "user": []}]       # unnamed: still pending, never "ok"


def _deps_line(deps: list) -> str:
    zh = "；".join(x["name"] + (f"（另有副本：{'、'.join(x['user'])}，未进入自检 PATH，请检查目录权限/真实路径）" if x["user"] else "（本机没找到）") for x in deps)
    return (f"{META}依赖待补 dependencies missing on the self-check PATH {VERIFY_PATH}: {zh}。不阻断安装：装好后 Agent 补齐再重跑自检，"
            "不用再签名 / not a blocker: after the install the Agent adds them and reruns the self-check, no new signature")


def _verify_outcome(name, version, deps, vran, out) -> None:
    if deps:
        print(f"状态 status: {plaza_deps.PENDING[0]} / {plaza_deps.PENDING[1]}", file=out)
        print(plaza_deps.task_text(name, version, deps, False, VERIFY_PATH), file=out)
        print(plaza_deps.task_text(name, version, deps, True, VERIFY_PATH), file=out)
    elif vran:
        print(f"{plaza_deps.PASSED[0]} / {plaza_deps.PASSED[1]} · {plaza_deps.ACCEPT[0]} / {plaza_deps.ACCEPT[1]}", file=out)


def pending_of(st, name: str) -> dict | None:
    recs = [r for r in read_installed(st) if r["name"] == name and (r.get("verify") or {}).get("state") == "pending"]
    return recs[-1] if recs else None


def run_reverify(name: str, *, harness=None, st=None, out=None, local=False, post=None, get=None, call=None) -> int:
    """#466 `agentj plaza verify`: same sha256 + installed files → rerun the self-test, no new signature (ADR-#466)."""
    out = out or sys.stdout
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise PlazaError("包名不对 / not a package name")
    if not local:
        from .state import State
        res = (call or plaza_perms._call)(st or State(), {"cmd": "plaza_verify", "name": name, "harness": harness}, REQUEST_WAIT)
        if res is not None and isinstance(res.get("rc"), int):
            print((res.get("out") or "").rstrip("\n"), file=out)
            return res["rc"]
        if fence.inside() or res is not None:
            print("没有重跑：Agent J 主机没在运行或版本太旧（agentj service start / 更新）/ not rerun: the Agent J host is not "
                  "running or too old", file=out)
            return EXIT_UNAVAILABLE
    st = st or _state()
    recs = [r for r in read_installed(st) if r["name"] == name and r["type"] != "card" and (harness is None or r["harness"] == harness)]
    v = (recs[-1].get("verify") if recs else None) or {}
    if not recs or not isinstance(v.get("paths"), list) or not isinstance(v.get("files"), str):
        raise PlazaError(f"{name} 没有可重跑的安装记录：用 agentj plaza request {name} --replace 重新安装 / no install record to "
                         f"rerun: agentj plaza request {name} --replace")
    r = recs[-1]
    again = f"agentj plaza request {name} --replace"
    data = _cache_get(st, r["sha256"])
    if data is None:
        try:
            _, data, _, _ = fetch(st, name, r["version"], post=post, get=get)
        except PlazaError as e:
            print(f"没有重跑：取不到当初签名的那个包 / not rerun: the signed package is not available ({e})", file=out)
            return EXIT_ERROR
    if bundle.sha256(data) != r["sha256"]:
        print(f"refused: 现在的包不是主人签名安装的那个（sha256 变了），不重跑；要装新版用 {again} / not the bundle the owner "
              f"signed for: {again}", file=out)
        return EXIT_DIGEST
    tree = plaza_deps.read_tree(r["target"], v["paths"])
    if tree is None or plaza_deps.tree_sha(tree) != v["files"]:
        print(f"refused: 装好的文件和签名安装时不一样了（被改过或缺了），不重跑；用 {again} 重新安装 / the installed files changed "
              f"since the signed install: {again}", file=out)
        return EXIT_DIGEST
    m, files = bundle.parse(data)
    s = summarize(m)
    try:
        vcmds = parse_verify(m["install"]["verify"], certified=bool(r["certified"])) if s["verify"] else []
        _verify_files_shipped(m, files)
    except VerifyRefused:
        vcmds = None
    deps, sandbox, vran = plaza_deps.missing(plaza_deps.wanted(s, vcmds), VERIFY_PATH), sandbox_kind(), False
    if vcmds and sandbox:
        rc, tail = _sandboxed(st, m, files, sandbox, bool(r["certified"]))
        if _dep_exit(rc, tail):
            deps = _add_tail(deps, tail)
        elif rc != 0:
            print(f"install.verify 失败（exit {rc}）/ failed (exit {rc}). Its output (data):", file=out)
            print("\n".join([FENCE_OPEN, *_t("\n".join(tail.splitlines()[-20:])), FENCE_CLOSE]), file=out)
            _record_install(st, {**r, "verify": {**v, "state": "failed", "missing": []}})
            return EXIT_ERROR
        else:
            vran = True
    _record_install(st, {**r, "verify": {**v, "state": "pending" if deps else "ok" if vran else "not_run",
                                         "missing": [x["name"] for x in deps]}})
    st.log("plaza_pkg_verify", name=name, result="pending" if deps else "ok")
    _verify_outcome(name, r["version"], deps, vran, out)
    if not deps and not vran:
        print("自检没有运行（没有自检命令或本机没有沙箱）/ the self-check did not run (none declared, or no sandbox here)", file=out)
    return EXIT_ERROR if deps else EXIT_OK


def _perm_lines(perm: dict, inferred: bool, allow, src: str, extra: list, under: list = ()) -> list[str]:
    out = [f"{META}权限 permissions（" + ("包没写，按内容推断 / not declared: inferred from the content" if inferred else
                                         "包自己声明的 + 按内容推断的 / declared by the package + inferred from the content")
           + f"）: {plaza_perms.describe(perm)}"]
    if under:
        out.append(f"{META}⚠ 包声明的少于实际内容 / THE PACKAGE DECLARES LESS THAN ITS CONTENT SHOWS —— 按内容还找到这些，照样算进去、照样要你确认 / "
                   "found in the content as well, counted and asked like the rest:")
        out += [f"{META}  • {' / '.join(plaza_perms.item_text(it))}" for it in under]
    out.append(f"{META}本机允许范围的依据 / allowed here per: {src}"
               + ("（读不到，全部当作要你确认 / unreadable: every item needs your yes）" if allow is None else ""))
    if not extra:
        return out + [f"{META}都在你的 Agent 现在允许的范围内 / all within what your Agent may do now"]
    out.append(f"{META}超出你的 Agent 现在允许的 / BEYOND what your Agent may do now —— 确认安装时手机上会弹一张确认卡，你签名允许才装 / "
               "at confirm a card comes to your phone; it installs only after you allow it:")
    for it in extra:
        zh, en = plaza_perms.item_text(it)
        out.append(f"{META}  • {zh} / {en}")
    return out


REFUSALS = ("denied", "timeout", "no_signature", "no_device", "estop", "serve_stop", "group_source", "owner_grant_scope",
            "too_many", "busy", "secret_path", "queue_full")


def _next_step(name, *, version, harness, workspace, params, params_file, sign_as, replace) -> str:
    """#428: what the Agent does after the owner agrees — ask the host; never send the owner to a terminal."""
    q = shlex.quote
    parts = ["agentj", "plaza", "request", name, *(["--version", version] if version else []), "--harness", harness,
             *(["--workspace", q(workspace)] if workspace else []), *[x for p in params or [] for x in ("--param", q(p))],
             *(["--params-file", q(params_file)] if params_file else []), *(["--sign-as", q(sign_as)] if sign_as else []),
             *(["--replace"] if replace else [])]
    return ("NOT INSTALLED. 预览给你的人看，他同意后你运行 " + " ".join(parts) + "：主机给他的遥控器发卡，他允许后由主机安装，"
            "不用他去终端敲命令 / once they agree, run that: one card on their remote, the host installs")


REQUEST_WAIT = 600            # per answer line: fetch + the card (ASK_TTL) + install.verify + install


def run_request(name, *, version=None, harness=None, workspace=None, params=None, params_file=None, sign_as=None,
                replace=False, st=None, out=None, call=None) -> int:
    """#428 `agentj plaza request`: ask the Agent J host to install `name`. This process downloads nothing, writes nothing
    and runs nothing: serve does every check, sends ONE signed card to the owner's remote and installs after the allow.
    #437: `name` may be a list (≤ 25): one card approves the group, each package gets its own receipt; per-package options
    (--version / --param / --params-file / --sign-as) only for a single package. Requests queue on the host (FIFO).
    Exit = the install's exit (0 installed; 6 not allowed / no card possible, with the real reason; 3 no answer yet)."""
    from . import humane, names, plaza_host
    from .state import State
    out = out or sys.stdout
    group = list(name) if isinstance(name, (list, tuple)) else [name]
    if len(group) > plaza_host.GROUP_MAX or len(set(group)) != len(group):
        raise PlazaError(f"一次最多 {plaza_host.GROUP_MAX} 个不重复的包名 / at most {plaza_host.GROUP_MAX} distinct package names")
    if len(group) > 1 and (version or params or params_file or sign_as):
        raise PlazaError("多个包一起请求时不能带 --version / --param / --params-file / --sign-as：带参数的工作流单独请求 / "
                         "several packages take no per-package option: request a workflow with parameters on its own")
    st = st or State()
    ws = workspace or os.environ.get("AGENT_WORKSPACE")
    req = {"cmd": "plaza_install", **({"name": group[0]} if len(group) == 1 else {"names": group}), "version": version,
           "harness": harness, "workspace": str(workspace_root(ws)) if ws else None, "params": list(params or []),
           "params_obj": read_params_file(params_file) if params_file else None, "sign_as": sign_as, "replace": bool(replace)}
    say = lambda key, **kw: humane.say("plaza.install." + key, "zh", **kw) + " / " + humane.say("plaza.install." + key, "en", **kw)
    shown = " ".join(group)
    print(f"已请 Agent J 主机安装 {shown}：主机先校验签名、审计和权限，再给主人的遥控器发一张确认卡 / asked the Agent J host to install "
          f"{shown}: it checks signature, audit and permissions, then sends one card to the owner's remote", file=out, flush=True)
    last = [None]

    def progress(ev):
        stage = ev.get("stage")
        key = (stage, ev.get("position"), ev.get("ahead"), ev.get("done"))
        if key == last[0]:
            return                                  # a keep-alive: nothing new to say
        last[0] = key
        if stage == "card":
            secs = int(ev.get("ttl") or 120)
            print(say("sent" if ev.get("online") else "waiting", secs=secs), file=out, flush=True)
            print("（这条命令会等到主人处理；命令若被提前结束，卡照样有效，结果会出现在聊天里 / this command waits for the "
                  "owner; if it is cut short the card still stands and the result shows in the chat）", file=out, flush=True)
        elif stage == "queued":
            n = ev.get("ahead") if ev.get("card") else max(0, int(ev.get("position") or 1) - 1)
            print(say("queued", n=n), file=out, flush=True)
        elif stage == "checking" and ev.get("done") != ev.get("total"):
            print(say("checking", done=ev.get("done"), total=ev.get("total")), file=out, flush=True)
    try:
        res = (call or plaza_perms._call)(st, req, REQUEST_WAIT, progress)
    except names.ServeBusy:
        print(say("cli_timeout"), file=out)
        return EXIT_UNAVAILABLE
    if res is None:
        print(say("not_running") + "（agentj service start）", file=out)
        return EXIT_PERMISSION
    if not isinstance(res.get("rc"), int) or res.get("why") == "bad_request":      # an older serve, or a refused shape
        print("没有安装：Agent J 主机不认这个请求（版本太旧？先更新 Agent J）/ NOT INSTALLED: the host does not know this request "
              f"(an older Agent J? update it) ({clean_line(str(res.get('why') or res.get('error')), 40)})", file=out)
        return EXIT_ERROR
    if res.get("out"):
        print(res["out"].rstrip("\n"), file=out)
    elif res.get("why") in REFUSALS:
        print(_perm_refusal(group[0], res), file=out)
    for r in res.get("results") or ():               # a group: one receipt line per package
        why = r.get("why")
        print(f"  ✓ {r['name']}：已安装 / installed" if r.get("rc") == 0 else
              f"  ✗ {r['name']}：没有安装 / NOT installed（"
              + (humane.say("plaza.install." + why, "zh") if why in REFUSALS else f"exit {r.get('rc')}") + "）", file=out)
    return res["rc"]


def _perm_refusal(name: str, res, items: bool = True, publisher: bool = False, audit: bool = False) -> str:
    if res is None:                      # no serve: no card can reach the phone; say which consent it needed
        zh = "、".join(x for x, on in (("要的权限超出你的 Agent 现在允许的", items), ("发布方换了", publisher),
                                       ("官方签名包被静态审计拦下", audit)) if on)
        en = ", ".join(x for x, on in (("needs more than your Agent may do now", items), ("its publisher changed", publisher),
                                       ("the static audit blocks this officially signed package", audit)) if on)
        return (f"没有安装：这个包{zh}，要你在手机上签名同意（命令行加旗标也不行），但 Agent J 没在运行，确认卡发不到你的手机。"
                f"先打开 Agent J，再让 Agent 运行 agentj plaza request {name} …，手机上会弹出确认卡，你点「允许」就会装 / "
                f"NOT INSTALLED: the package {en}; only your signed yes on the phone accepts that (no flag replaces it), but "
                f"Agent J is not running, so no card can reach your phone. Start Agent J, then have your Agent run "
                f"agentj plaza request {name} …: a card comes to your phone and it installs once you allow it.")
    if res.get("why") in REFUSALS:
        from . import humane
        key = "plaza.install." + res["why"]
        return humane.template(key, "zh") + " / " + humane.template(key, "en")
    return f"没有安装：权限确认没完成 / NOT INSTALLED: the permission check did not complete ({clean_line(str(res.get('why')), 40)})"

def _install_card(st, m, files, sha, certified, downgrade, *, name, version, replace, accept_unverified, owner_confirmed, digest,
                  post, out, pubr=None, findings=(), audit_blk=(), perm_call=None, host_ask=None) -> int:
    """P432: a card package → the owner's deck, switched OFF until the owner reads its permission summary on the phone."""
    from . import jcards, jcards_pkg
    pubr = pubr or {"known": True, "changed": False, "prev": None, "fp": None}
    probs = jcards_pkg.preflight(files)
    if probs:
        print("refused: 这张卡没通过上线检查，什么都没装 / the card fails the checks, nothing installed:\n  " + "\n  ".join(probs[:20]), file=out)
        return EXIT_ERROR
    doc = json.loads(files["card/card.json"])
    cid, target = doc["id"], f"card/{doc['id']}"
    have = jcards.find(st.root, cid)
    mine = have and not have["official"] and (jcards_pkg.source(st.root, cid) or {}).get("name") == m["name"]
    if have and not mine:
        raise PlazaError(f"这台 Agent J 主机上已有一张 id 为 {cid} 的卡，不是从这个包装的，不覆盖 / a card {cid} exists here that this package did not install: never replaced")
    dg = install_digest(sha, pathlib.Path(target), "card", {}, {"replace": bool(replace)})
    if host_ask is not None:
        owner_confirmed, digest = True, dg
    if not owner_confirmed:
        print("\n".join([FENCE_OPEN, f"{META}{NOTE}",
                         f"{META}安装预览 install preview: {m['name']} · {_kind('card')} · v{m['version']} · " + (CERT_BADGE if certified else UNVERIFIED),
                         f"{META}包 bundle sha256 {sha} · {len(files)} 个文件 files",
                         *([f"{META}{downgrade}"] if downgrade else []),
                         *market_guard.publisher_lines(pubr, certified),
                         *market_guard.audit_lines(findings, certified),
                         f"{META}标题 title:", *_t(m["title_zh"]), *_t(m["title_en"]),
                         f"{META}这张卡能做什么 what the card can do（权限摘要 / permission summary）:",
                         *_t(jcards_pkg.summary_text(doc, "zh", None)), *_t(jcards_pkg.summary_text(doc, "en", None)),
                         f"{META}装到 target: 手机卡片 {target}" + ("（已装过，--replace 换新版，旧版休眠可回滚 / installed: --replace puts the old one to sleep）" if have else ""),
                         f"{META}装上后先关着：主人在手机 设置 → Cards 看过权限摘要再打开 / installed switched OFF: the owner reads the summary on the phone, then turns it on",
                         FENCE_CLOSE]), file=out)
        if not certified:
            print("这是待审核的社群卡：没有 Agent J 官方签名。确认时必须加 --accept-unverified / an UNVERIFIED community card: the confirm needs --accept-unverified", file=out)
        print(f"digest: {dg}", file=out)
        flags = ("" if certified else "--accept-unverified ") + f"--owner-confirmed --digest {dg}"
        print(_next_step(name, version=version, harness="card", workspace=None, params=None, params_file=None, sign_as=None,
                         replace=replace).replace(" --harness card", "")
              + f"（主人在终端自己确认也可：同一条 agentj plaza install 加 {flags} / the owner at a terminal: the same install + {flags}）",
              file=out)
        return EXIT_OK
    if digest != dg:
        print("refused: this is not the install your human confirmed (digest mismatch). Run the preview again.", file=out)
        return EXIT_DIGEST
    if not certified and not accept_unverified and host_ask is None:
        raise PlazaError("待审核的社群卡要加 --accept-unverified（你的人看过并同意了）/ an unverified community card needs --accept-unverified")
    if have and not replace:
        raise PlazaError(f"{target} 已装过；换新版加 --replace / installed already: add --replace for the new version")
    pub_grant = audit_grant = None
    if pubr["changed"] or audit_blk or host_ask is not None:
        res = host_ask({"name": m["name"], "version": m["version"], "type": "card", "sha256": sha, "digest": dg,
                        "certified": certified, "publisher": pubr, "perm": plaza_perms.EMPTY, "extra": [], "under": False,
                        "audit": [{k: f[k] for k in ("rule", "path", "line", "level")} for f in list(findings)[:30]],
                        "target": target, "harness": "card", "replace": bool(have and replace), "verify": None}) \
            if host_ask is not None else plaza_perms.ask_owner(
                st, m["name"], m["version"], sha, [], call=perm_call,
                publisher={"prev": pubr["prev"], "new": pubr["fp"]} if pubr["changed"] else None, audit=audit_blk or None)
        if res is None or not res.get("ok"):
            print(_perm_refusal(name, res, False, pubr["changed"], bool(audit_blk)), file=out)
            return EXIT_PERMISSION
        g = {"rid": res.get("rid"), "device": res.get("device")}
        pub_grant = g if pubr["changed"] else None
        audit_grant = {**g, "findings": [a["rule"] for a in audit_blk]} if audit_blk else None
    r = jcards_pkg.install(st.root, files, {"name": m["name"], "version": m["version"], "sha256": sha, "certified": certified})
    if not r.get("ok"):
        print("refused: " + "; ".join(r.get("problems") or [e.get("en", "") for e in r.get("errors", [])] or [str(r.get("why"))]), file=out)
        return EXIT_ERROR
    _record_install(st, {"name": m["name"], "type": "card", "version": m["version"], "sha256": sha, "harness": "card", "target": target,
                         "category": m.get("category"), "certified": certified,
                         **({"publisher_grant": pub_grant} if pub_grant else {}),
                         **({"audit_grant": audit_grant} if audit_grant else {}),
                         "installed_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
    st.log("plaza_pkg_install", name=m["name"], result="ok")
    print(f"已安装 / installed: {m['name']} {m['version']} → {target}（关着 / switched off）" + (f"；旧版休眠为 {r['dormant']}，回滚 agentj card rollback {cid} / the old one sleeps: agentj card rollback {cid}" if r.get("dormant") else ""), file=out)
    if not pubr["known"] or pub_grant:
        market_guard.record_publisher(st, m["name"], pubr["fp"])
    print("告诉你的人：在手机 设置 → Cards 找到它，看过权限摘要再打开 / tell your human: Settings → Cards, read the summary, then switch it on", file=out)
    srv = doc.get("server") if isinstance(doc.get("server"), dict) else None
    if srv:
        print(f"它的本机服务没装：源码 {srv['repo']} @ {srv['ref']}，端口 {srv['port']}；按你的人的意思再装 / its local service is NOT installed "
              f"(source {srv['repo']} @ {srv['ref']}, port {srv['port']}); install it only if your human asks", file=out)
    try:
        _call(st, "installed", {"nonce": _nonce(), "name": m["name"], "version": m["version"]}, post)
    except PlazaError as e:
        print(f"warning: 安装计数没报上去（不影响安装）/ the install count was not reported (the install is fine): {e}", file=out)
    return EXIT_OK


def _todo(m: dict, s: dict, target: pathlib.Path, workspace, st, out) -> None:
    lines = ["还要你的人做的事 / what your human still has to do（下面引用的是包作者的文字 / quoted lines are the author's text）:"]
    envfile = workspace_root(workspace) / ".env"
    body = []
    if s["env"]:
        body.append(f"{META}环境变量 env：填到 / fill in {envfile}（只追加缺的，密钥绝不贴进对话 / append missing names only; never paste secrets into a chat）:")
        for e in s["env"]:
            body += _t(("[密钥 SECRET] " if e["secret"] else "") + e["name"] + ("" if e["required"] else " (optional)"))
    if s["accounts"]:
        body.append(f"{META}第三方账号 accounts:")
        for a in s["accounts"]:
            body += _t(a["name"] + (f" — {a['url']}" if a["url"] else ""))
    if s["post_install"]:
        body.append(f"{META}安装后命令（agentj 没运行；在 {target} 里由你的人决定是否运行）/ post_install, NOT run by agentj:")
        for c in s["post_install"]:
            body += _t(c)
    if s["skills"]:
        have = {r["name"] for r in read_installed(st)}
        body.append(f"{META}依赖的技能 dependent skills（每个单独一张确认卡 / each with its own card）:")
        for dep in s["skills"]:
            body.append(f"{META}  agentj plaza request {dep}" + ("（本机已装 installed）" if dep in have else ""))
    if s["automations"]:
        body.append(f"{META}定时任务 {len(s['automations'])} 个全部休眠：第一次手动跑通后再由你的人启用 / {len(s['automations'])} "
                    "automations stay dormant until your human enables them after a first manual run")
    if body:
        print("\n".join([*lines, FENCE_OPEN, *body, FENCE_CLOSE]), file=out)


# ------------------------------------------------------------------ publish
def _manifest_strings(m, path="manifest"):
    if isinstance(m, str):
        yield path, m
    elif isinstance(m, list):
        for i, x in enumerate(m):
            yield from _manifest_strings(x, f"{path}[{i}]")
    elif isinstance(m, dict):
        for k, v in m.items():
            if path == "manifest" and k == "files":
                continue
            yield from _manifest_strings(v, f"{path}.{k}")


# Package mode of layer 1 (mirror of worker/src/scan.ts scanPackageText; shared cases protocol/vectors/package-scan.json).
# Package files are code: `password = args.password`, a test's `OPENROUTER_API_KEY='file-key'`. Token-shaped secrets, private
# keys, e-mails, phones, home paths, this machine's names, private IPs and our ids still count; the "name = value" shapes, URL
# passwords, Authorization headers and high-entropy hits count only when the value itself looks like a literal secret.
_SOFT = ("credential_assignment", "url_credentials", "authorization_header", "high_entropy")
_PLACEHOLDER = re.compile(r"example|sample|dummy|fake|placeholder|redacted|changeme|change[_-]?me|your[_-]|xxx|test|<|\.\.\.", re.I)
_CRED_VALUE = re.compile(r"(?<![A-Za-z0-9])(?:api[_-]?key|apikey|secret|token|password|passwd|pass|pwd|pw|auth|credentials?|access[_-]?key"
                         r"|client[_-]?secret|private[_-]?key|[A-Za-z0-9_-]*[_-](?:key|secret|token|pass|passwd|pwd|pw)|[A-Za-z0-9]*(?:key|secret|token))"
                         r"[\"']?\s*[:=]\s*([\"']?)([^\s\"'<>]{8,})", re.I | re.ASCII)
_URL_PASS = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/:@]+:([^\s/@]+)@", re.I | re.ASCII)
_AUTH_VALUE = re.compile(r"\bAuthorization\s*:\s*(?:Bearer|Basic|token)\s+([^\s\"'<>]+)", re.I | re.ASCII)
_WORDY = re.compile(r"(?:[a-z]{4,}[_\-./=]){2,}")


def literal_secret(v: str) -> bool:
    """≥ 12 printable ASCII characters, no brackets / operators / backticks, not an expression (a.b.c), not lower-case words
    joined by - or _ (file-key, app_password) or an UPPER_NAME, no placeholder word, ≥ 2 of lower / upper / digit."""
    s = re.sub(r"[\"'`,;)\]}]+$", "", re.sub(r"^[\"'`]+", "", v))
    if len(s) < 12 or not re.fullmatch(r"[\x21-\x7e]+", s) or re.search(r"[()\[\]{}<>$%*|,;\\`]", s):
        return False
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+", s):
        return False
    if re.fullmatch(r"[a-z]+(?:[-_][a-z]+)*", s) or re.fullmatch(r"[A-Z]+(?:_[A-Z0-9]+)*", s) or _PLACEHOLDER.search(s):
        return False
    return sum(bool(re.search(c, s)) for c in ("[a-z]", "[A-Z]", "[0-9]")) >= 2


def _entropy(t: str) -> float:
    import math
    return -sum(n / len(t) * math.log2(n / len(t)) for n in collections.Counter(t).values())


def package_scan(text: str, host: str | None, user: str | None) -> collections.Counter:
    """Layer 1 hits of one package text (file or manifest string), package mode (see above)."""
    _, hits = privacy.redact_with_report(text, host, user)
    hits = collections.Counter({k: n for k, n in hits.items() if k not in _SOFT})
    n = sum(literal_secret(m.group(2)) for m in _CRED_VALUE.finditer(text))
    if n:
        hits["credential_assignment"] += n
    for kind, rx in (("url_credentials", _URL_PASS), ("authorization_header", _AUTH_VALUE)):
        n = sum(literal_secret(m.group(1)) for m in rx.finditer(text))
        if n:
            hits[kind] += n
    for m in re.finditer(r"[A-Za-z0-9+/_=-]{32,}", text):
        t = m.group(0)
        if re.fullmatch(r"[0-9a-fA-F-]+", t) or _WORDY.search(t) or not (re.search("[A-Z]", t) and re.search("[a-z]", t) and re.search("[0-9]", t)):
            continue
        if _entropy(t) >= 4.3:
            hits["high_entropy"] += 1
    return hits


def layer1(manifest: dict, files: dict, identity) -> list[tuple[str, str, int]]:
    """Every UTF-8 file, every path and every manifest string through privacy layer 1. → [(where, kind, n)] (empty = clean)."""
    host, user = identity if identity is not None else privacy.local_identity()
    found = []
    for where, text in [("file names", "\n".join(sorted(files)))] + list(_manifest_strings(manifest)):
        hits = package_scan(text, host, user)
        found += [(where, k, n) for k, n in sorted(hits.items())]
    for p in sorted(files, key=lambda x: x.encode()):
        try:
            text = files[p].decode("utf-8")
        except UnicodeDecodeError:
            continue
        hits = package_scan(text, host, user)
        found += [(p, k, n) for k, n in sorted(hits.items())]
    return found


def layer2_draft(m: dict, files: dict) -> tuple[dict, str]:
    """A bounded draft for Jev: manifest titles / summaries / tags + README.md + SKILL.md (≤ 20 000 characters)."""
    draft = {"manifest": {k: m.get(k) for k in ("name", "type", "version", "title_zh", "title_en", "summary_zh", "summary_en", "tags")}}
    budget = DRAFT_MAX - len(json.dumps(draft, ensure_ascii=False))
    said = ["manifest titles / summaries / tags"]
    for p in ("README.md", "SKILL.md"):
        if p in files and budget > 0:
            t = files[p].decode("utf-8", "replace")
            draft[p] = t[:budget]
            said.append(f"{p} ({len(draft[p])} of {len(t)} characters)")
            budget -= len(draft[p])
    return draft, ", ".join(said)


def _publish_call(st, fields: dict, signed: dict, post, out) -> tuple[dict, dict | None]:
    """POST publish with the host's own signature (P426 #47). A server from before #47 refuses the unknown fields with
    400 bad_request: then once more without them, said out loud — the package is then unsigned, which every verifier sees
    (the operator's auto-certify leaves it to a human; installers show 「未签名」)."""
    try:
        status, obj = cloud.plaza_pkg_call(st, "publish", {**fields, **signed}, **({"post": post} if post else {}))
    except cloud.CloudError as e:
        raise PlazaError(ERRORS.get(e.kind) or f"连不上 Agent J 服务器 / cannot reach the Agent J server ({e.kind})")
    if status == 400 and cloud.parse_error(obj) == "bad_request":
        print("注意 / note: 服务器还不支持发布方签名，这次不带签名发布 / the server does not take publisher signatures yet: "
              "publishing unsigned", file=out)
        return _call(st, "publish", {**fields, "nonce": _nonce()}, post), None
    if 200 <= status < 300:
        return obj, signed
    e = cloud.parse_error(obj) or f"http_{status}"
    raise PlazaError(f"{ERRORS.get(e, e)} (HTTP {status})")


def publish_digest(sha: str, show_name: bool) -> str:
    return hashlib.sha256(json.dumps({"sha256": sha, "show_name": bool(show_name)}, sort_keys=True).encode()).hexdigest()[:16]


def run_publish(directory: str, *, show_name=False, owner_confirmed=False, digest=None, st=None, post=None, put=None,
                transport=None, env=None, identity=None, out=None) -> int:
    out = out or sys.stdout
    if owner_confirmed and not (digest and _DIGEST.fullmatch(digest)):
        raise PlazaError("--owner-confirmed 要和预览时打印的 --digest 一起用 / --owner-confirmed needs the --digest printed by the preview")
    st = st or _state()
    if not cloud.read_cloud(st):
        raise PlazaError(ERRORS["unlinked"])
    try:
        data, m = bundle.build(directory, {"certified": False, "author": "community"})
        _, files = bundle.parse(data)
        perm_inferred = "permissions" not in m                 # P426 #181-1: no declaration → the inferred one is published
        if perm_inferred:
            data, m = bundle.build(directory, {"certified": False, "author": "community",
                                               "permissions": plaza_perms.infer(m, files)})
    except bundle.BundleError as e:
        raise PlazaError(f"包不合格，没发 / not a valid package, nothing sent: {e}")
    _, files = bundle.parse(data)
    if m["type"] == "card":           # P432 上线关键点: only a card that passes every check goes out
        from . import jcards_pkg
        probs = jcards_pkg.preflight(files)
        if probs:
            print("这张卡没通过上线检查，什么都没发 / the card fails the checks, nothing sent:\n  " + "\n  ".join(probs[:20]), file=out)
            return EXIT_BLOCKED
    sha = bundle.sha256(data)
    findings = plaza_audit.audit(files, m)                                          # P426 #181-2: before anything else
    if plaza_audit.blocked(findings):
        print(market_guard.refusal(findings, "什么都没发"), file=out)
        return EXIT_BLOCKED
    warns = market_guard.audit_lines(findings, False) if findings else []
    hits = layer1(m, files, identity)
    if hits:
        print("layer 1 找到了可能的隐私 / 密钥，什么都没发；请你的人改原文件后重来（agentj 绝不替你改代码）/ layer 1 found possible "
              "private data or secrets — nothing sent; fix the files and run again (agentj never rewrites them):", file=out)
        for where, kind, n in hits[:200]:
            print(f"  {where}: {kind} ×{n}", file=out)
        return EXIT_BLOCKED
    d = publish_digest(sha, show_name)
    if owner_confirmed:
        if digest != d:
            print("refused: the package is not the one your human confirmed (digest mismatch — a file or --show-agent-name "
                  "changed). Run the preview again.", file=out)
            return EXIT_DIGEST
        fields = {"nonce": _nonce(), "name": m["name"], "type": m["type"], "version": m["version"], "sha256": sha,
                  "bytes": len(data), "show_name": bool(show_name)}
        signed = plaza_trust.sign(st.signing_key(), m["name"], m["type"], m["version"], sha, int(time.time()))
        obj, signed = _publish_call(st, fields, signed, post, out)
        up = obj.get("upload") if isinstance(obj.get("upload"), dict) else {}
        try:
            status, ans = cloud.plaza_upload(st, up.get("url"), data, **({"put": put} if put else {}))
        except cloud.CloudError as e:
            raise PlazaError("上传地址不是本机配置的 API，没传 / the upload URL is not on the configured API: not uploaded"
                             if e.kind == "refused_url" else f"上传失败 / upload failed ({e.kind})")
        if status == 422 and cloud.parse_error(ans) == "audit_blocked":
            raise PlazaError(f"{ERRORS['audit_blocked']} — {clean_line(str(ans.get('path', '')), 120)} line "
                             f"{clean_line(str(ans.get('line', '')), 10)}: {clean_line(str(ans.get('rule', '')), 40)} (HTTP 422)")
        if status == 422 and cloud.parse_error(ans) == "secret_found":
            raise PlazaError(f"{ERRORS['secret_found']} — {clean_line(str(ans.get('path', '')), 120)}: "
                             f"{clean_line(str(ans.get('kind', '')), 40)} (HTTP 422)")
        if not 200 <= status < 300:
            e = cloud.parse_error(ans) or f"http_{status}"
            raise PlazaError(f"上传被拒 / upload refused: {ERRORS.get(e, e)} (HTTP {status})")
        st.log("plaza_pkg_publish", name=m["name"], result="ok")
        state = ans.get("state") if ans.get("state") in ("live", "uploading", "rejected") else "?"
        print(f"已公开发到广场 / published: {m['name']} {m['version']} · state {state}（社群 · 待审核 / community · pending review）", file=out)
        if signed:
            fp = plaza_trust.fingerprint(plaza_trust.unb64u(signed["host_pubkey"], 32))
            print(f"发布方签名 / publisher signature: {plaza_trust.short_fp(fp)}（本机身份密钥签的，别人安装时会核对；完整指纹 / full "
                  f"fingerprint {fp}）", file=out)
        print(f"别人这样装 / others install it with: agentj plaza request {m['name']}", file=out)
        if warns:
            print("\n".join(["发布回执里的审计提示（装的人也会看到）/ audit notes in this receipt (installers see them too):",
                             FENCE_OPEN, *warns, FENCE_CLOSE]), file=out)
        return EXIT_OK
    draft, said = layer2_draft(m, files)
    v = privacy.layer2(draft, env=env, transport=transport, gate="plaza")
    name = st.agent_name() if show_name else None
    print("=" * 8 + " 将公开发到广场的包 — 所有付费客户的 Agent 与员工都能下载 / the package that would be PUBLISHED to every customer "
          + "=" * 8, file=out)
    print("\n".join([FENCE_OPEN, f"{META}{NOTE}",
                     f"{META}{m['name']} · {_kind(m['type'])} · v{m['version']} · 社群 · 待审核 community · pending review "
                     f"(certified=false, author=community 由 agentj 强制 / forced by agentj)",
                     f"{META}bundle sha256 {sha} · {len(data)} bytes · {len(files)} 个文件 files",
                     f"{META}标题 title:", *_t(m["title_zh"]), *_t(m["title_en"]), f"{META}简介 summary:", *_t(m["summary_zh"]),
                     *_t(m["summary_en"]), *_summary_lines(summarize(m)), f"{META}全部文件 every file:",
                     *_t("\n".join(f"{p} ({len(files[p])} B)" for p in sorted(files, key=lambda x: x.encode()))), FENCE_CLOSE]), file=out)
    print(f"作者显示为 shown as: " + (f"Agent「{name}」@ 你们账号的别名 your account's alias" if name else
                                    "匿名 Agent @ 你们账号的别名 / unnamed Agent @ your account's alias"), file=out)
    print("layer 1: nothing found (every text file, every path, every manifest string)", file=out)
    print("权限声明 permissions: " + plaza_perms.describe(plaza_perms.parse(m["permissions"])) + (
          "（manifest.json 里没写，这是按包内容推断的，会写进发布出去的 manifest；不对就在 manifest.json 里自己写 permissions 再重来 / "
          "not in manifest.json: inferred from the content and published inside the manifest; if it is wrong, write "
          "\"permissions\" into manifest.json and run again）" if perm_inferred else "（manifest.json 里声明的 / declared in manifest.json）"),
          file=out)
    under = [] if perm_inferred else plaza_perms.excess(plaza_perms.infer(m, files), plaza_perms.parse(m["permissions"]))
    if under:                                                  # S1: installers count these anyway; say it before it is public
        print("⚠ 包声明的少于实际内容 / the declaration is less than the content shows（装的人那边会照样算进去并要主人确认 / installers "
              "count these anyway and ask their owner）: " + "; ".join(plaza_perms.item_text(it)[0] for it in under), file=out)
    print("\n".join([FENCE_OPEN, *warns, FENCE_CLOSE]) if warns else "静态审计 static audit: 没发现问题 / nothing found", file=out)
    fp = plaza_trust.fingerprint(st.signing_pub())
    print(f"发布方签名 / publisher signature: 确认后用这台主机自己的身份密钥签名，指纹 {plaza_trust.short_fp(fp)}（私钥不离开本机）/ "
          "signed at confirm with this host's own identity key (the private key never leaves this machine)", file=out)
    print(f"layer 2 saw / 发给 Jev 的: {said}", file=out)
    print(plaza._verdict_line(v), file=out)
    print(f"digest: {d}", file=out)
    print("NOT SENT. 把上面的文件清单和内容给你的人看；只有他明确同意公开后才运行 / Show your human the package above; only after they "
          f"explicitly agree to publish it: agentj plaza publish {shlex.quote(str(directory))} "
          + ("--show-agent-name " if show_name else "") + f"--owner-confirmed --digest {d}", file=out)
    return EXIT_OK if v.status == "ok" else EXIT_BLOCKED if v.status == "blocked" else EXIT_UNAVAILABLE


# ------------------------------------------------------------------ argparse (plaza.py calls these)
def add_arguments(ps, search, show, report) -> None:
    search.add_argument("--type", choices=["all", "qa", "skill", "workflow", "card"], default="all",
                        help="默认 all：先技能 / 工作流，再问答 / default all: packages first, then Q&A")
    search.add_argument("--sort", choices=list(SORTS), help="包的排序（默认：认证优先再按本周安装）/ package order")
    search.add_argument("--tag", help="包的标签 / package tag")
    tr = search.add_mutually_exclusive_group()
    tr.add_argument("--official", action="store_true", help="只看官方包 / official packages only")
    tr.add_argument("--community", action="store_true", help="只看社群包 / community packages only")
    show.add_argument("--version", help="包的版本（只对包）/ package version")
    s = ps.add_parser("install", help="从广场装技能 / 工作流（先预览，--owner-confirmed --digest 才装）/ install a skill or workflow "
                      "(preview first)")
    s.add_argument("name")
    s.add_argument("--version")
    s.add_argument("--harness", choices=list(HARNESSES), help="默认 = 接的 Agent / default: the configured Agent")
    s.add_argument("--workspace", help="工作流落地的工作区（默认 $AGENT_WORKSPACE 或 ~/agent-workspace）/ workflow workspace")
    s.add_argument("--param", action="append", default=[], metavar="K=V", help="工作流参数（可多次）/ workflow parameter (repeatable)")
    s.add_argument("--params-file", help="JSON 对象的参数文件 / a JSON object of parameters")
    s.add_argument("--accept-unverified", action="store_true", help="你的人同意装未认证的社群包 / your human accepts an unverified package")
    s.add_argument("--sign-as", help="以这个称呼签署工作流文档（否则 verified: []）/ sign the workflow documents in this name")
    s.add_argument("--replace", action="store_true", help="目标是 agentj 为这个包装的旧版时，把它移到状态目录的 plaza/backups/ / "
                   "move a target agentj installed for this package to the backups")
    s.add_argument("--skip-verify", action="store_true", help="你的人决定不运行包的自检 install.verify / your human chose not to "
                   "run the package's self-test")
    s.add_argument("--owner-confirmed", action="store_true", help="你的人看过预览并同意安装 / your human saw the preview and agreed")
    s.add_argument("--digest", help="预览打印的 digest / the digest the preview printed")
    s = ps.add_parser("request", help="请 Agent J 主机安装（主人手机上签一张确认卡，主机来装；Agent 不带确认旗标）/ ask the Agent J "
                      "host to install: one signed card on the owner's remote, the host installs")
    s.add_argument("name", nargs="+", help="包名；多个 = 一张卡批一组（最多 25 个）/ package names; several = one card for the group")
    s.add_argument("--version")
    s.add_argument("--harness", choices=list(HARNESSES), help="默认 = 接的 Agent / default: the configured Agent")
    s.add_argument("--workspace", help="工作流落地的工作区 / workflow workspace")
    s.add_argument("--param", action="append", default=[], metavar="K=V", help="工作流参数（可多次）/ workflow parameter (repeatable)")
    s.add_argument("--params-file", help="JSON 对象的参数文件（本命令读，内容交给主机）/ a JSON object of parameters")
    s.add_argument("--sign-as", help="以这个称呼签署工作流文档 / sign the workflow documents in this name")
    s.add_argument("--replace", action="store_true", help="替换 agentj 为这个包装的旧版 / replace this package's earlier install")
    s = ps.add_parser("publish", help="把一个包目录公开发到广场（两层隐私闸 + 预览，--owner-confirmed --digest 才发）/ publish a "
                      "package folder (privacy gate + preview)")
    s.add_argument("dir")
    s.add_argument("--show-agent-name", action="store_true", help="署上本机 Agent 名（默认匿名）/ show this Agent's name")
    s.add_argument("--owner-confirmed", action="store_true", help="你的人看过预览并同意公开 / your human saw the preview and agreed")
    s.add_argument("--digest", help="预览打印的 digest / the digest the preview printed")
    s = ps.add_parser("like", help="给包点赞（每个账号一票）/ like a package (one per account)")
    s.add_argument("name")
    s.add_argument("--off", action="store_true", help="取消点赞 / unlike")
    s = ps.add_parser("verify", help="依赖补齐后重跑已装包的自检（同一包同一 digest，不用再签名）/ rerun an installed package's "
                      "self-check after adding its dependencies (same package and digest, no new signature)")
    s.add_argument("name")
    s.add_argument("--harness", choices=list(HARNESSES))
    s = ps.add_parser("installed", help="本机从广场装过的包 / what this machine installed from the plaza")
    s.add_argument("--json", action="store_true")
    s = ps.add_parser("partners", help="优先推荐伙伴（推荐第三方服务前先查；链接旁必须带披露）/ preferred partners: check before "
                      "recommending a service; the disclosure goes right next to the link")
    s.add_argument("--slot", help="只看一个槽位，如 tts、voice-clone / one slot, e.g. tts")
    s.add_argument("--json", action="store_true")


def cmd(a, run) -> bool:
    """Dispatch the package commands; False = not ours (plaza.py handles it)."""
    c = a.plaza_cmd
    if c == "search":
        track = "official" if a.official else "community" if a.community else None
        run(run_search, a.words, type_=a.type, sort=a.sort, tag=a.tag, track=track, limit=a.limit, as_json=a.json)
    elif c == "show":
        run(run_show, a.id, version=a.version, as_json=a.json)
    elif c == "mine":
        run(run_mine, as_json=a.json)
    elif c == "report":
        run(run_report, a.id, a.reason)
    elif c == "install":
        run(run_install, a.name, version=a.version, harness=a.harness, workspace=a.workspace, params=a.param,
            params_file=a.params_file, accept_unverified=a.accept_unverified, sign_as=a.sign_as, replace=a.replace,
            skip_verify=a.skip_verify, owner_confirmed=a.owner_confirmed, digest=a.digest)
    elif c == "request":
        run(run_request, a.name, version=a.version, harness=a.harness, workspace=a.workspace, params=a.param,
            params_file=a.params_file, sign_as=a.sign_as, replace=a.replace)
    elif c == "publish":
        run(run_publish, a.dir, show_name=a.show_agent_name, owner_confirmed=a.owner_confirmed, digest=a.digest)
    elif c == "like":
        run(run_like, a.name, not a.off)
    elif c == "installed":
        run(run_installed, as_json=a.json)
    elif c == "verify":
        run(run_reverify, a.name, harness=a.harness)
    elif c == "partners":
        run(run_partners, a.slot, as_json=a.json)
    else:
        return False
    return True
