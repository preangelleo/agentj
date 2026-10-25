#!/usr/bin/env python3
"""web-report：把 JSON 数据套进固定模板，生成网页报告并发布到自己的 Cloudflare 二级域名。

  templates | schema <模板> | example <模板> | recommend "<描述>"
  validate <data.json> [--template R03|<自定义模板目录>] [--json]   # 不过关：逐条字段错误，退出码 2
  render   <data.json> [--template …] [--email] --out ./preview|out.html   # 离线；--email 出邮件安全版（单个 HTML）
  new-template <目录>                                       # 自定义模板脚手架（模板 + JSON Schema + 示例）
  （不写 --template 时用官方默认模板 R15 Agent J Noir；协议全文见 references/protocol.md）
  init     --domain example.com [--sub report] [--author-email you@example.com] [--mail mailgun|resend|none] [--dry-run] [--upgrade]
  publish  <data.json> --template R03 --expires 7d --access public|code[:访问码]|email-domain:example.com|email-any [--dry-run]
           → 报告链接 + 读者访问码 + 统计页 /<id>/dashboard + 管理授权码，并自动发一封回执给作者本人
  list | stats <id> | revoke <id> | extend <id> --expires 30d | set-access <id> --access ... | set-admin-code <id>
  doctor | --self-test

纯标准库（Python 3.10+）。退出码：0 成功；2 参数/数据错误；3 配置/凭据缺失；4 网络或云端错误。
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import secrets
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import wr_cloud  # noqa: E402
import wr_custom  # noqa: E402
import wr_email  # noqa: E402
import wr_render  # noqa: E402
import wr_schemas  # noqa: E402
from wr_templates import RENDERERS, Ctx  # noqa: E402
from wr_validate import validate  # noqa: E402

PKG = HERE.parent
TPL = PKG / "templates"
MAX_ASSET = 90 * 1024 * 1024
CODE_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


class UserError(Exception):
    pass


def registry() -> dict:
    return json.loads((TPL / "registry.json").read_text(encoding="utf-8"))


def find_template(name: str | None) -> dict:
    """内置编号（R15 / R15-noir / noir）、自定义模板目录路径，或 report-templates/ 下的自定义模板 id；不写 = 默认模板。"""
    raw = str(name or "").strip()
    n = (raw or wr_schemas.DEFAULT_TEMPLATE).lower()
    for t in registry()["templates"]:
        if n in (t["id"].lower(), t["ref"].lower(), t["slug"]):
            return t
    try:
        if "/" in raw or raw.startswith((".", "~")) or Path(raw).expanduser().is_dir():
            return wr_custom.load(raw)
        if (wr_custom.templates_root() / raw / "template.json").is_file():
            return wr_custom.load(wr_custom.templates_root() / raw)
    except wr_custom.TemplateError as e:
        raise UserError(str(e))
    raise UserError(f"没有模板 {name!r}；运行 templates 查看全部（自定义模板写目录路径，或放进 {wr_custom.templates_root()}）")


def load_schema(t: dict) -> dict:
    if t.get("custom"):
        return t["schema_obj"]
    return json.loads((PKG / t["schema"]).read_text(encoding="utf-8"))


def load_data(path: str) -> tuple[dict, Path]:
    p = Path(path).expanduser()
    try:
        return json.loads(p.read_text(encoding="utf-8")), p.resolve().parent
    except FileNotFoundError:
        raise UserError(f"找不到数据文件：{p.name}")
    except ValueError as e:
        raise UserError(f"数据文件不是合法 JSON：{e}")


def check(data: dict, t: dict) -> list[str]:
    return validate(data, load_schema(t))


def ensure_valid(data, t: dict) -> None:
    if not isinstance(data, dict):
        raise UserError("数据必须是一个 JSON 对象")
    errs = check(data, t)
    if errs:
        raise UserError("数据不符合模板 schema：\n  " + "\n  ".join(errs[:30]))


def render_email(data: dict, t: dict) -> str:
    """邮件安全版：先严格校验，再按模板的邮件渲染器出单个 HTML（table + 内联样式）。"""
    ensure_valid(data, t)
    if t.get("custom"):
        try:
            return wr_custom.render(data, t, email=True)
        except wr_custom.TemplateError as e:
            raise UserError(str(e))
    if t["id"] not in wr_email.EMAIL_RENDERERS:
        have = "、".join(wr_email.EMAIL_RENDERERS)
        raise UserError(f"{t['ref']} 只有网页版，没有邮件版（有邮件版的模板：{have}）；去掉 --email，或换模板")
    return wr_email.render_email(data, t)


def render(data: dict, t: dict, base: Path | None, *, expires_at=None, public=True) -> tuple[str, dict, Ctx]:
    ensure_valid(data, t)
    if t.get("custom"):
        html = wr_custom.render(data, t, email=False)
        return html, {"meta": {"title": data.get("title"), "template": t["ref"], "expires_at": expires_at}, "data": data}, Ctx(base)
    ctx = Ctx(base)
    res = RENDERERS[t["id"]](data, ctx)
    ctx.assets[wr_render.WEBMCP_FILE] = wr_render.WEBMCP_SRC  # WebMCP 脚本同源外置，页面里不写 CSP 哈希
    body, sections, css = (res + ("",))[:3] if len(res) == 2 else res
    html = wr_render.page(data=data, template=t, body=body, sections=sections, expires_at=expires_at, public=public,
                          frames=ctx.frames, wide=t["id"] in ("R05", "R08"), extra_css=css)
    payload = {"meta": {"title": data.get("title"), "template": t["ref"], "expires_at": expires_at}, "sections": sections, "data": data}
    return html, payload, ctx


def write_bundle(out: Path, html: str, payload: dict, ctx: Ctx) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(html, encoding="utf-8")
    (out / "report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    files = [out / "index.html", out / "report.json"]
    for name, src in ctx.assets.items():
        if not src.is_file():
            raise UserError(f"找不到媒体文件：{src.name}（路径相对 data.json 所在目录）")
        if src.stat().st_size > MAX_ASSET:
            raise UserError(f"媒体文件太大（>90MB）：{src.name}；请改用 https 外链")
        (out / "assets").mkdir(exist_ok=True)
        shutil.copyfile(src, out / "assets" / name)
        files.append(out / "assets" / name)
    return files


def parse_expires(s: str, now: datetime | None = None) -> str | None:
    now = now or datetime.now(timezone.utc)
    s = str(s or "").strip().lower()
    if s in ("never", "forever", "永久"):
        return None
    m = re.fullmatch(r"(\d{1,4})\s*([hdwmy])", s)
    if not m:
        raise UserError("有效期写法：1d / 7d / 30d / 1y / 2y / 3y / never（也可 12h、2w、6m）")
    n, u = int(m.group(1)), m.group(2)
    if n <= 0:
        raise UserError("有效期必须大于 0")
    delta = {"h": timedelta(hours=n), "d": timedelta(days=n), "w": timedelta(weeks=n),
             "m": timedelta(days=30 * n), "y": timedelta(days=365 * n)}[u]
    if delta > timedelta(days=365 * 20):
        raise UserError("有效期最长 20 年；要一直保留请用 never")
    return (now + delta).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_code(n=6) -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(n))


def new_admin_code() -> str:
    """管理授权码：12 位分三段，便于抄写（如 k7m2-q9xa-3fbn）。"""
    raw = new_code(12)
    return "-".join(raw[i:i + 4] for i in range(0, 12, 4))


def parse_access(s: str, mail_provider: str = "none") -> tuple[dict, str | None]:
    """返回 (access 字典, 明文访问码或 None)。访问码只存哈希，明文只出现在本次输出和发给作者的邮件里。"""
    s = str(s or "").strip()
    if s == "public":
        return {"mode": "public"}, None
    head = s.split(":", 1)[0]
    if head in ("code", "password"):  # password 是旧写法，保留兼容
        given = s.split(":", 1)[1] if ":" in s else ""
        code = os.environ.get("WEB_REPORT_ACCESS_CODE", "") if given in ("", "auto") else given
        code = code or new_code(6)
        if not (4 <= len(code) <= 64):
            raise UserError("访问码长度 4–64")
        return {"mode": "code", "pw": wr_cloud.hash_code(code)}, code
    if s == "email-any" or s.startswith("email-domain:"):
        if mail_provider not in ("mailgun", "resend"):
            raise UserError("邮箱验证码需要发信服务：init 时加 --mail mailgun（推荐）或 --mail resend")
        if s == "email-any":
            return {"mode": "email-any"}, None
        doms = [d.strip().lower().lstrip("@") for d in s.split(":", 1)[1].split(",") if d.strip()]
        if not doms or not all(re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,24}", d) for d in doms):
            raise UserError("email-domain:后面写公司域名，可逗号分隔，如 email-domain:example.com")
        return {"mode": "email-domain", "domains": doms}, None
    raise UserError("访问方式：public | code[:访问码] | email-domain:<域名> | email-any")


def new_id() -> str:
    return new_code(12)


EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{1,190}\.[A-Za-z]{2,24}$")


def resolve_author_email(flag: str | None, cfg: dict | None = None) -> tuple[str | None, str]:
    """作者邮箱来源优先级：命令行 → 环境变量 → 配置文件 → Agent J 主人联系卡 0 号位。返回 (邮箱, 来源)。"""
    for value, source in ((flag, "flag"), (os.environ.get("WEB_REPORT_AUTHOR_EMAIL"), "env"),
                          ((cfg or {}).get("owner_email"), "config")):
        if value:
            if not EMAIL_RE.match(value.strip()):
                raise UserError("作者邮箱格式不对")
            return value.strip().lower(), source
    found = wr_cloud.owner_email_from_agentj()
    if found and EMAIL_RE.match(found):
        return found.lower(), "agentj-owner-contact"
    return None, "missing"


def owner_mail(result: dict, title: str) -> tuple[str, str]:
    """发给作者本人的发布回执。作者本人就是这些码的主人，所以全部原样写出，不做打码。"""
    lines = [f"你的网页报告「{title}」已发布。", "",
             f"报告链接：{result['url']}"]
    if result.get("access_code"):
        lines.append(f"读者访问码：{result['access_code']}（和链接一起发给读者；建议分两条消息发）")
    elif result["access"].startswith("email"):
        doms = "、".join("@" + d for d in result.get("domains") or [])
        lines.append(f"读者访问方式：填邮箱收验证码{'（仅限 ' + doms + '）' if doms else '（任意邮箱）'}")
    else:
        lines.append("读者访问方式：全公开，拿到链接即可查看")
    lines += [f"有效期至：{result['expires_at'] if result['expires_at'] != 'never' else '永久'}", "",
              f"统计页（Dashboard）：{result['dashboard_url']}",
              f"管理授权码：{result['admin_code']}（只给你自己用，登录统计页；也可以用本邮箱收验证码登录）", "",
              "这封邮件只发给作者本人，请不要转发。"]
    return f"网页报告已发布：{title}", "\n".join(lines)


def recommend(text: str, top=3) -> list[tuple[int, dict]]:
    q = text.lower()
    scored = []
    for t in registry()["templates"]:
        score = sum(3 if len(k) >= 3 else 2 for k in t["keywords"] if k.lower() in q)
        score += sum(1 for w in re.findall(r"[\w一-鿿]{2,}", t["scenario_zh"]) if w in q)
        scored.append((score, t))
    scored.sort(key=lambda x: -x[0])
    best = [x for x in scored if x[0] > 0][:top]
    return best or [(0, find_template(wr_schemas.DEFAULT_TEMPLATE))]


def out(obj, as_json: bool):
    if as_json:
        print(json.dumps(obj, ensure_ascii=False, indent=2))
    elif isinstance(obj, str):
        print(obj)
    else:
        print(json.dumps(obj, ensure_ascii=False, indent=2))


# ------------------------------------------------------------------ 命令
def cmd_templates(a):
    reg = registry()
    custom = wr_custom.discover()
    if a.json:
        keep = ("id", "ref", "name_zh", "name_en", "scenario_zh", "scenario_en", "outputs", "dir", "broken")
        return out({"default": reg.get("default"), "templates": reg["templates"],
                    "custom_root": str(wr_custom.templates_root()),
                    "custom": [{k: c[k] for k in keep if k in c} for c in custom]}, True)
    for t in reg["templates"]:
        flag = "【默认】" if t.get("default") else ""
        outs = "网页+邮件" if "email" in t.get("outputs", []) else "仅网页"
        print(f'{t["ref"]:<14} {t["name_zh"]:<20} [{t["default_theme"]}] {outs}  {flag}{t["scenario_zh"]}')
    print(f"\n自定义模板（{wr_custom.templates_root()}）：" + ("" if custom else "还没有；用 new-template <目录> 建一个"))
    for c in custom:
        print(f'{c["ref"]:<14} ' + (f'✗ {c["broken"].splitlines()[0]}' if c.get("broken") else
                                    f'{c["name_zh"]}  {"网页+邮件" if "email" in c["outputs"] else "仅网页"}  {c["scenario_zh"]}'))


def cmd_schema(a):
    t = find_template(a.template)
    if t.get("custom"):
        return out(t["schema_obj"], True)
    print((PKG / t["schema"]).read_text(encoding="utf-8"))


def cmd_example(a):
    t = find_template(a.template)
    if t.get("custom") and not t.get("example"):
        raise UserError("这个自定义模板没有 example.json")
    print(Path(t["example"] if t.get("custom") else PKG / t["example"]).read_text(encoding="utf-8"))


def cmd_recommend(a):
    res = recommend(a.text)
    if a.json:
        return out([{"template": t["ref"], "name_zh": t["name_zh"], "score": s} for s, t in res], True)
    for s, t in res:
        print(f'{t["ref"]:<14} {t["name_zh"]}（匹配 {s}）— {t["scenario_zh"]}')


def cmd_validate(a):
    t = find_template(a.template)
    data, _ = load_data(a.data)
    errs = check(data, t) if isinstance(data, dict) else ["$: 应为 object"]
    if a.json:
        out({"ok": not errs, "template": t["ref"],
             "errors": [dict(zip(("path", "message"), e.split(": ", 1))) for e in errs]}, True)
        return 2 if errs else 0
    if errs:
        print(f"FAIL  {t['ref']}（{len(errs)} 处）\n  " + "\n  ".join(errs))
        return 2
    print(f"OK  {t['ref']}  {data.get('title')}")


def cmd_render(a):
    t = find_template(a.template)
    data, base = load_data(a.data)
    dest = Path(a.out).expanduser()
    if a.email:
        html = render_email(data, t)
        if dest.suffix.lower() not in (".html", ".htm"):
            dest = dest / "email.html"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(html, encoding="utf-8")
        print(f"已渲染 {t['ref']} 邮件版 → {dest.resolve()}（{len(html.encode())} 字节）")
        return
    html, payload, ctx = render(data, t, base, expires_at=None, public=True)
    if dest.suffix.lower() in (".html", ".htm"):   # 单个文件：资源放在同目录 assets/
        stage = Path(tempfile.mkdtemp(prefix="web-report-"))
        try:
            write_bundle(stage, html, payload, ctx)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(stage / "index.html", dest)
            if (stage / "assets").is_dir():
                shutil.copytree(stage / "assets", dest.parent / "assets", dirs_exist_ok=True)
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        print(f"已渲染 {t['ref']} → {dest.resolve()}")
        return
    files = write_bundle(dest, html, payload, ctx)
    print(f"已渲染 {t['ref']} → {dest.resolve() / 'index.html'}（{len(files)} 个文件）")


def cmd_new_template(a):
    try:
        d = wr_custom.scaffold(a.dir, a.id)
    except wr_custom.TemplateError as e:
        raise UserError(str(e))
    print(f"已建自定义模板 → {d.resolve()}\n  改 schema.json（数据长什么样）、page.html / email.html（长什么样），然后：\n"
          f"  validate {d}/example.json -t {d}    render {d}/example.json -t {d} --email -o out.html")


def cmd_init(a):
    env = wr_cloud.env_values(["CLOUDFLARE_API_TOKEN", "MAILGUN_API_KEY", "MAILGUN_DOMAIN", "MAILGUN_FROM", "MAILGUN_REGION",
                               "RESEND_API_KEY", "RESEND_FROM"], a.env_file)
    sub = a.sub.strip(".").lower()
    if not re.fullmatch(r"[a-z0-9-]{1,40}", sub):
        raise UserError("--sub 只能是字母数字连字符，如 report")
    script = a.script or f"web-report-{sub}"
    mb, missing = wr_cloud.mail_bindings(a.mail, env)
    if missing:
        raise UserError("选择了 --mail %s 但缺少变量：%s（写进工作区 .env，见 .env.example）" % (a.mail, ", ".join(missing)))
    try:
        old = wr_cloud.load_config()
    except wr_cloud.CloudError:
        old = {}
    author, author_src = resolve_author_email(a.author_email, old)
    if not author:
        raise UserError("还不知道作者邮箱：每次发布的回执（链接、访问码、统计页、管理授权码）要发到这里，"
                        "也用来登录统计页。请问用户要用哪个邮箱，然后加 --author-email 重跑。")
    plan = {"domain": a.domain, "hostname": f"{sub}.{a.domain}" if a.domain else f"{sub}.<你的域名>", "worker": script,
            "r2_bucket": a.bucket, "kv_namespace": f"{script}-otp", "d1_stats": None if a.no_stats else f"{script}-stats",
            "author_email": author, "author_email_source": author_src, "mail": a.mail, "cron": wr_cloud.CRON,
            "config": str(wr_cloud.config_path()), "mode": "upgrade（保留已有密钥）" if a.upgrade else "full"}
    if a.dry_run:
        print("计划（dry-run，未联网）：")
        return out(plan, True)
    if not env.get("CLOUDFLARE_API_TOKEN"):
        raise wr_cloud.CloudError("缺少 CLOUDFLARE_API_TOKEN（按 references/cloudflare-setup.md 建最小权限 token，写进工作区 .env）")
    cf = wr_cloud.Cloudflare(env["CLOUDFLARE_API_TOKEN"])
    cf.verify()
    zones = cf.zones()
    if not a.domain:
        print("这个 token 能看到的域名（选一个，再用 --domain 重跑）：")
        for z in zones:
            print("  ", z["name"])
        return 2
    zone = next((z for z in zones if z["name"] == a.domain), None)
    if not zone:
        raise UserError(f"token 看不到域名 {a.domain}：确认域名已在 Cloudflare 且 token 的 Zone Resources 包含它")
    account = zone["account"]["id"]
    hostname = f"{sub}.{a.domain}"
    steps = {"bucket": cf.ensure_bucket(account, a.bucket)}
    kv_id, steps["kv"] = cf.ensure_kv(account, f"{script}-otp")
    bindings = [{"type": "r2_bucket", "name": "REPORTS", "bucket_name": a.bucket},
                {"type": "kv_namespace", "name": "OTP", "namespace_id": kv_id}] + mb
    d1_id = None
    if not a.no_stats:
        d1_id, steps["d1"] = cf.ensure_d1(account, f"{script}-stats")
        bindings.append({"type": "d1", "name": "DB", "id": d1_id})
    if a.upgrade and old.get("admin_token"):
        token = old["admin_token"]
        cf.upload_worker(account, script, bindings, keep_secrets=True)
    else:
        token, digest = wr_cloud.new_admin_token()
        bindings += wr_cloud.secret_bindings(digest)
        cf.upload_worker(account, script, bindings)
    steps["worker"] = "deployed"
    cf.attach_domain(account, zone["id"], hostname, script)
    steps["domain"] = hostname
    cf.set_cron(account, script)
    steps["cron"] = wr_cloud.CRON
    cfg = {"base_url": f"https://{hostname}", "hostname": hostname, "zone_id": zone["id"], "account_id": account,
           "script": script, "bucket": a.bucket, "kv_id": kv_id, "d1_id": d1_id, "mail_provider": a.mail,
           "owner_email": author,
           "initialized_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat()}
    wr_cloud.attach_admin(cfg, token)
    p = wr_cloud.save_config(cfg)
    print(f"完成：https://{hostname}  （配置已存 {p.name}，权限 600；管理令牌不显示）")
    if a.mail == "none":
        print("注意：没配发信服务，发布回执不会自动发到作者邮箱，统计页也只能用管理授权码登录。")
    print("提示：证书签发可能需要 1–5 分钟，之后运行 doctor 检查。Cloudflare token 只在 init/升级时需要，可在控制台删除或设过期。")
    return out(steps, True)


def _cfg_admin():
    cfg = wr_cloud.load_config()
    return cfg, wr_cloud.Admin(cfg)


def cmd_publish(a):
    t = find_template(a.template)
    data, base = load_data(a.data)
    expires_at = parse_expires(a.expires)
    if a.dry_run:
        try:
            cfg = wr_cloud.load_config()
        except wr_cloud.CloudError:
            cfg = {"base_url": "https://report.example.com", "mail_provider": a.mail_hint or "none"}
    else:
        cfg = wr_cloud.load_config()
    access, code = parse_access(a.access, cfg.get("mail_provider", "none"))
    public = access["mode"] == "public"
    if not public and data.get("indexable"):
        raise UserError("非公开报告不能设置 indexable=true")
    owner, _ = resolve_author_email(a.author_email, cfg)
    rid = a.id or new_id()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{3,47}", rid):
        raise UserError("--id 只能是小写字母数字连字符，4–48 位")
    admin_code = new_admin_code()
    html, payload, ctx = render(data, t, base, expires_at=expires_at, public=public)
    tmp = Path(tempfile.mkdtemp(prefix="web-report-"))
    try:
        files = write_bundle(tmp, html, payload, ctx)
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        meta = {"id": rid, "title": data.get("title"), "template": t["ref"], "created_at": now, "expires_at": expires_at,
                "access": access, "hint": a.hint, "indexable": bool(public and data.get("indexable")),
                "av": secrets.randbelow(1 << 30), "dv": 0, "admin": wr_cloud.hash_code(admin_code),
                "owner_email": owner, "files": len(files)}
        base_url = cfg["base_url"].rstrip("/")
        result = {"url": f"{base_url}/{rid}/", "id": rid, "template": t["ref"], "expires_at": expires_at or "never",
                  "access": access["mode"], "dashboard_url": f"{base_url}/{rid}/dashboard", "admin_code": admin_code,
                  "author_email": owner, "files": len(files), "dry_run": bool(a.dry_run)}
        if access.get("domains"):
            result["domains"] = access["domains"]
        if code:
            result["access_code"] = code
        subject, body = owner_mail(result, data.get("title") or rid)
        if a.dry_run:
            result["preview"] = str(tmp / "index.html")
            result["author_email_sent"] = False
            tmp = None  # 保留预览目录
        else:
            adm = wr_cloud.Admin(cfg)
            for f in files:
                rel = f.relative_to(files[0].parent).as_posix()
                ctype = "text/html; charset=utf-8" if rel == "index.html" else (mimetypes.guess_type(f.name)[0] or "application/octet-stream")
                adm.put_file(rid, rel, f.read_bytes(), ctype)
            adm.put_meta(rid, meta)  # meta 最后写：写入前报告对外一直是 404
            # 一条龙最后一步：回执发给作者本人（链接、访问码、统计页、管理授权码原样写出，不打码）。
            sent = {"sent": False, "reason": "--no-email"} if a.no_email or not owner else adm.notify_owner(rid, subject, body)
            if not owner:
                sent["reason"] = "no author email"
            result["author_email_sent"] = bool(sent.get("sent"))
            if not sent.get("sent"):
                result["author_email_note"] = f"回执没有发出（{sent.get('reason')}）；请把下面的回执通过主人自己的渠道转交，不要发到群里。"
                result["author_receipt"] = body
        out(result, True)
        if code or admin_code:
            print("访问码给读者，管理授权码只给作者自己；两者都只显示这一次（作者邮箱回执里也有）。", file=sys.stderr)
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


def cmd_list(a):
    _, adm = _cfg_admin()
    reps = sorted(adm.list(), key=lambda r: r.get("created_at") or "", reverse=True)
    if a.json:
        return out(reps, True)
    for r in reps:
        print(f'{r["id"]:<14} {r.get("state", "?"):<8} {r["access"]["mode"]:<13} {str(r.get("expires_at") or "never")[:10]:<10} '
              f'{r.get("template", ""):<14} {r.get("title") or ""}')
    if not reps:
        print("（还没有报告）")


def cmd_revoke(a):
    _, adm = _cfg_admin()
    out(adm.revoke(a.id), True)


def cmd_extend(a):
    _, adm = _cfg_admin()
    out(adm.patch(a.id, {"expires_at": parse_expires(a.expires)}), True)


def cmd_set_access(a):
    cfg, adm = _cfg_admin()
    access, code = parse_access(a.access, cfg.get("mail_provider", "none"))
    res = adm.patch(a.id, {"access": access})
    if code:
        res["access_code"] = code
    out(res, True)


def cmd_set_admin_code(a):
    """换一个新的管理授权码（旧码和已登录的统计页会话立即失效）。"""
    _, adm = _cfg_admin()
    fresh = new_admin_code()
    res = adm.patch(a.id, {"admin": wr_cloud.hash_code(fresh)})
    res["admin_code"] = fresh
    out(res, True)


def cmd_stats(a):
    cfg, adm = _cfg_admin()
    s = adm.stats(a.id)
    if a.json or not s.get("enabled"):
        return out(s, True)
    t = s["totals"]
    print(f"阅读 {t['views']} 次 · 独立访客 {t['visitors']} · 验证邮箱 {t['emails']} · 访问码错误 {s['kinds'].get('unlock_fail', 0)} 次")
    for d in s["daily"][-7:]:
        print(f"  {datetime.fromtimestamp(d['day'] * 86400, timezone.utc).date()}  {d['views']:>5} 次  {d['visitors']:>4} 人")
    if s["emails"]:
        print(f"邮箱名单（{len(s['emails'])} 个，只给作者看）：")
        for e in s["emails"][:20]:
            print(f"  {e['email']}  {e['views']} 次")
    print(f"完整统计页：{cfg['base_url'].rstrip('/')}/{a.id}/dashboard")


def cmd_doctor(a):
    rep = {"config": str(wr_cloud.config_path()), "templates": registry()["count"]}
    try:
        cfg = wr_cloud.load_config()
        rep.update(base_url=cfg["base_url"], mail=cfg.get("mail_provider"), admin_token="present" if cfg.get("admin_token") else "missing",
                   author_email=cfg.get("owner_email") or "missing", stats="d1" if cfg.get("d1_id") else "off")
        rep["worker"] = wr_cloud.Admin(cfg).ping()
    except (wr_cloud.CloudError, OSError) as e:
        rep["problem"] = str(e)
        out(rep, True)
        return 3
    out(rep, True)


def self_test() -> int:
    """离线：全部模板的 schema 自洽、示例通过校验并能渲染、反例被拒、调色板封闭、关键解析函数正确。"""
    reg = registry()
    allowed = wr_render.ALLOWED_HEX  # 调色板 + brand-themes.json 登记的品牌色
    n = 0
    for t in reg["templates"]:
        schema = load_schema(t)
        assert schema == wr_schemas.schema_for(next(x for x in wr_schemas.TEMPLATES if x["id"] == t["id"])), f"{t['ref']} schema 与源不一致"
        data, base = load_data(str(PKG / t["example"]))
        assert not check(data, t), f"{t['ref']} 示例不合法: {check(data, t)[:3]}"
        bad = dict(data)
        bad.pop("title")
        bad["bogus"] = 1
        assert len(check(bad, t)) >= 2, f"{t['ref']} 反例没被拒"
        html, _, ctx = render(data, t, base)
        for h in re.findall(r"#[0-9a-fA-F]{6}\b", html.split('id="wr-data"')[0]):
            assert h.lower() in allowed, f"{t['ref']} 用了调色板外的颜色 {h}"
        assert f'src="assets/{wr_render.WEBMCP_FILE}"' in html and 'type="application/ld+json"' in html
        assert ctx.assets.get(wr_render.WEBMCP_FILE) == wr_render.WEBMCP_SRC
        assert "sha256-" not in html and wr_render.esc("script-src 'self'") in html
        assert "<script>alert" not in html
        n += 1
    base_data, base_dir = load_data(str(PKG / find_template("R01")["example"]))
    for theme in wr_render.THEMES:  # 主题与版式分离：任何模板都能换任何主题
        html, _, _ = render({**base_data, "theme": theme}, find_template("R01"), base_dir)
        assert f'data-theme="{theme}"' in html
        for h in re.findall(r"#[0-9a-fA-F]{6}\b", html.split('id="wr-data"')[0]):
            assert h.lower() in allowed, f"主题 {theme} 用了未登记的颜色 {h}"
    assert "document.modelContext" in wr_render.WEBMCP_SRC.read_text(encoding="utf-8")
    evil = {"title": "<script>alert(1)</script>", "decisions": [{"text": "[x](javascript:alert(1))"}]}
    h, _, _ = render(evil, find_template("R01"), None)
    assert "<script>alert" not in h.split('id="wr-data"')[0] and "javascript:" not in h.split('id="wr-data"')[0]
    assert parse_expires("never") is None and parse_expires("7d").endswith("Z")
    for bad in ("0d", "abc", "99y"):
        try:
            parse_expires(bad)
            raise AssertionError(bad)
        except UserError:
            pass
    stored, shown = parse_access("code")
    assert stored["mode"] == "code" and len(shown) == 6 and shown not in json.dumps(stored)
    assert re.fullmatch(r"[a-z2-9]{4}-[a-z2-9]{4}-[a-z2-9]{4}", new_admin_code())
    assert parse_access("email-domain:example.com", "mailgun")[0]["domains"] == ["example.com"]
    assert recommend("帮我把今天的融资路演整理一下")[0][1]["id"] == "R03"
    print(f"self-test OK：{n} 个模板 × {len(wr_render.THEMES)} 个主题渲染与校验通过，颜色封闭，转义与解析正常（离线，无凭据）")
    return 0


def cmd_build_templates(a):
    """维护者用：从 wr_schemas.py 重新生成 registry.json 与每个模板的 schema.json。"""
    (TPL / "registry.json").write_text(json.dumps(wr_schemas.registry(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for t in wr_schemas.TEMPLATES:
        d = TPL / wr_schemas.template_ref(t)
        d.mkdir(exist_ok=True)
        (d / "schema.json").write_text(json.dumps(wr_schemas.schema_for(t), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已生成 {len(wr_schemas.TEMPLATES)} 个模板的 schema 与 registry.json")


def main(argv=None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] in ("--self-test", "self-test"):
        return self_test()
    ap = argparse.ArgumentParser(prog="web_report.py", description="网页报告：JSON + 模板 → 带访问控制的分享网址")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("templates", help="列出全部模板")
    for name in ("schema", "example"):
        p = sp.add_parser(name)
        p.add_argument("template")
    p = sp.add_parser("recommend", help="按描述推荐模板")
    p.add_argument("text")
    for name in ("validate", "render", "publish"):
        p = sp.add_parser(name)
        p.add_argument("data")
        p.add_argument("--template", "-t", help=f"模板编号或自定义模板目录（默认 {wr_schemas.DEFAULT_TEMPLATE}）")
        if name == "render":
            p.add_argument("--out", "-o", default="./web-report-preview")
            p.add_argument("--email", action="store_true", help="邮件安全版（table + 内联样式，≤640px）")
        if name == "publish":
            p.add_argument("--expires", "-e", default="7d")
            p.add_argument("--access", "-a", default="code")
            p.add_argument("--hint", help="门禁页提示语（不要写访问码本身）")
            p.add_argument("--id", help="自定义报告 id（默认随机 12 位）")
            p.add_argument("--dry-run", action="store_true", help="只渲染与校验，不上传")
            p.add_argument("--author-email", help="作者邮箱（默认用 init 时登记的）")
            p.add_argument("--no-email", action="store_true", help="不给作者发回执邮件")
            p.add_argument("--mail-hint", help=argparse.SUPPRESS)
    p = sp.add_parser("init", help="在自己的 Cloudflare 上部署报告服务")
    p.add_argument("--domain")
    p.add_argument("--sub", default="report")
    p.add_argument("--bucket", default="web-reports")
    p.add_argument("--script")
    p.add_argument("--mail", choices=["mailgun", "resend", "none"], default="none")
    p.add_argument("--env-file")
    p.add_argument("--upgrade", action="store_true", help="只更新 Worker 代码，保留管理令牌与密钥")
    p.add_argument("--author-email", help="作者邮箱：接收发布回执、登录统计页（默认读 Agent J 主人联系卡）")
    p.add_argument("--no-stats", action="store_true", help="不建 D1 统计库（统计页将不可用）")
    p.add_argument("--dry-run", action="store_true")
    sp.add_parser("list")
    p = sp.add_parser("revoke")
    p.add_argument("id")
    p = sp.add_parser("extend")
    p.add_argument("id")
    p.add_argument("--expires", "-e", required=True)
    p = sp.add_parser("stats", help="看某份报告的访问统计")
    p.add_argument("id")
    p = sp.add_parser("set-admin-code", help="换管理授权码")
    p.add_argument("id")
    p = sp.add_parser("set-access")
    p.add_argument("id")
    p.add_argument("--access", "-a", required=True)
    p = sp.add_parser("new-template", help="自定义模板脚手架")
    p.add_argument("dir")
    p.add_argument("--id", help="模板 id（默认用目录名）")
    sp.add_parser("doctor")
    sp.add_parser("build-templates", help=argparse.SUPPRESS)
    as_json = "--json" in argv   # --json 写在子命令前后都行
    a = ap.parse_args([x for x in argv if x != "--json"])
    a.json = as_json
    fn = globals()["cmd_" + a.cmd.replace("-", "_")]
    try:
        return fn(a) or 0
    except UserError as e:
        print(f"错误：{e}", file=sys.stderr)
        return 2
    except wr_cloud.CloudError as e:
        print(f"错误：{e}", file=sys.stderr)
        return 3 if "初始化" in str(e) or "缺少" in str(e) or "权限" in str(e) else 4
    except OSError as e:
        print(f"网络或文件错误：{type(e).__name__}: {e}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    sys.exit(main())
