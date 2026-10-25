"""自定义模板：用户自己的模板目录，走和内置模板同一套协议（JSON → schema 校验 → 渲染）。

目录（`new-template` 生成）：
  template.json   {"schema": "web-page-report.template/v1", "id": "my-daily", "name_zh": …, "name_en": …, "description_zh": …}
  schema.json     数据的 JSON Schema（draft 2020-12 子集，见 SUPPORTED；用了不支持的关键字直接报错，不假装校验过）
  example.json    一份能通过校验的示例数据
  page.html       网页版模板（必需）
  email.html      邮件版模板（可选；没有就只出网页）

模板语言是「无逻辑」的小子集——没有任何能执行代码或原样输出 HTML 的写法：
  {{ path }}            转义后的文字（path 用点号：sections.0.heading；{{ . }} = 当前项；{{ @index }} = 当前序号，从 1 开始）
  {{ path | md }}       转义后再做行内 Markdown（**粗体** `代码` [链接](https://…) 与单双引号强调）
  {{ path | paras }}    转义后按空行分段，「- 」开头的段落变列表
  {{ path | url }}      只放行 https:// 与 mailto: 的网址，其它变成 #（href 里必须用它）
  {{#each list}} … {{/each}}     {{#if x}} … {{/if}}     {{#unless x}} … {{/unless}}
安全：模板文件里不许有 <script> / <iframe> / <object> / <embed> / <link> / <base> / <form> / <meta http-equiv>、on* 事件属性、
javascript: 网址、@import、外链 url(…) / src / srcset；页面加 CSP（default-src 'none'，图片只许 data:）。所以自定义模板既不能
跑脚本，也不能从外面加载任何东西。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from wr_render import esc, inline, paras, safe_url
from wr_validate import validate

MANIFEST = "web-page-report.template/v1"
MAX_TEMPLATE = 256 * 1024
SUPPORTED = {"$schema", "$id", "title", "description", "default", "examples", "type", "enum", "const", "minLength", "maxLength",
             "pattern", "minimum", "maximum", "required", "properties", "additionalProperties", "items", "minItems", "maxItems",
             "oneOf", "anyOf"}
CSP = ("default-src 'none'; img-src data:; style-src 'unsafe-inline'; font-src data:; base-uri 'none'; form-action 'none'; "
       "frame-src 'none'; script-src 'none'; connect-src 'none'")
_ID = re.compile(r"[a-z0-9][a-z0-9-]{1,40}")
_BANNED = [
    (re.compile(r"<\s*(script|iframe|object|embed|applet|link|base|form|frame|frameset|portal|svg\s[^>]*\bon)", re.I), "不允许的标签"),
    (re.compile(r"<\s*meta[^>]+http-equiv", re.I), "不允许 <meta http-equiv>"),
    (re.compile(r"\son[a-z]+\s*=", re.I), "不允许 on* 事件属性"),
    (re.compile(r"(java|vb)script\s*:", re.I), "不允许 javascript: 网址"),
    (re.compile(r"@import", re.I), "不允许 @import"),
    (re.compile(r"url\(\s*['\"]?(?!data:|#)", re.I), "url(…) 只许 data:"),
    (re.compile(r"\s(src|poster|background|data|xlink:href)\s*=\s*['\"]?(?!data:|\{\{)", re.I), "src 只许 data: 或 {{ … | url }}"),
    (re.compile(r"\ssrcset\s*=", re.I), "不允许 srcset"),
    (re.compile(r"<\s*style[^>]*>[^<]*\{\{", re.I), "<style> 里不能放 {{ }}"),
    (re.compile(r"\sstyle\s*=\s*['\"][^'\"]*\{\{", re.I), "style 属性里不能放 {{ }}"),
]
_HREF_TAG = re.compile(r"\s(href|src|poster)\s*=\s*['\"]?\{\{\s*([^}|]+?)\s*(\|\s*(\w+)\s*)?\}\}", re.I)
_TOKEN = re.compile(r"\{\{\s*([#/^]?)\s*(each|if|unless)?\s*([^}|]*?)\s*(?:\|\s*(\w+)\s*)?\}\}")


class TemplateError(Exception):
    pass


def templates_root() -> Path:
    """自定义模板默认放在 $AGENT_WORKSPACE/report-templates/（默认 ~/agent-workspace/report-templates）。"""
    w = os.environ.get("AGENT_WORKSPACE") or "~/agent-workspace"
    return Path(os.path.expanduser(w)) / "report-templates"


def check_schema(schema, path="$") -> list[str]:
    errs = []
    if isinstance(schema, dict):
        for k, v in schema.items():
            if k not in SUPPORTED:
                errs.append(f"{path}: 不支持的 schema 关键字 {k}（只支持 {', '.join(sorted(SUPPORTED))}）")
                continue
            if k == "properties" and isinstance(v, dict):
                for name, sub in v.items():
                    errs += check_schema(sub, f"{path}.properties.{name}")
            elif k in ("items", "additionalProperties") and isinstance(v, dict):
                errs += check_schema(v, f"{path}.{k}")
            elif k in ("oneOf", "anyOf") and isinstance(v, list):
                for i, sub in enumerate(v):
                    errs += check_schema(sub, f"{path}.{k}[{i}]")
    return errs


def check_template_text(text: str, name: str) -> list[str]:
    errs = []
    if len(text.encode("utf-8")) > MAX_TEMPLATE:
        errs.append(f"{name}: 超过 {MAX_TEMPLATE // 1024} KB")
    for rx, why in _BANNED:
        m = rx.search(text)
        if m:
            line = text.count("\n", 0, m.start()) + 1
            errs.append(f"{name} 第 {line} 行: {why}")
    for m in _HREF_TAG.finditer(text):
        if (m.group(4) or "").lower() != "url":
            errs.append(f"{name}: {m.group(1)}=\"{{{{ {m.group(2)} }}}}\" 必须写成 {{{{ {m.group(2)} | url }}}}")
    try:
        parse(text)
    except TemplateError as e:
        errs.append(f"{name}: {e}")
    return errs


def load(path) -> dict:
    """读一个模板目录并做全部静态检查；任何问题都抛 TemplateError（带具体文件与原因）。"""
    d = Path(path).expanduser().resolve()
    if not d.is_dir():
        raise TemplateError(f"没有这个模板目录：{path}")
    try:
        man = json.loads((d / "template.json").read_text(encoding="utf-8"))
        schema = json.loads((d / "schema.json").read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise TemplateError(f"模板目录缺文件：{Path(e.filename).name}（需要 template.json、schema.json、page.html）")
    except ValueError as e:
        raise TemplateError(f"template.json / schema.json 不是合法 JSON：{e}")
    errs = []
    if man.get("schema") != MANIFEST:
        errs.append(f'template.json: "schema" 必须是 "{MANIFEST}"')
    tid = str(man.get("id") or "")
    if not _ID.fullmatch(tid) or re.fullmatch(r"r\d{2}(-.*)?", tid):
        errs.append("template.json: id 只能是小写字母数字和连字符（2–41 位），且不能用 R01–R99 这类内置编号")
    if not isinstance(schema, dict) or schema.get("type") != "object":
        errs.append('schema.json: 顶层必须是 {"type": "object", …}')
    errs += check_schema(schema)
    files = {}
    for name in ("page.html", "email.html"):
        f = d / name
        if f.is_symlink():
            errs.append(f"{name}: 不能是符号链接")
            continue
        if f.is_file():
            files[name] = f.read_text(encoding="utf-8")
            errs += check_template_text(files[name], name)
    if "page.html" not in files:
        errs.append("缺 page.html")
    if errs:
        raise TemplateError("自定义模板没通过检查：\n  " + "\n  ".join(errs))
    return {"id": tid, "ref": tid, "slug": tid, "custom": True, "dir": str(d), "name_zh": man.get("name_zh") or tid,
            "name_en": man.get("name_en") or tid, "scenario_zh": man.get("description_zh") or "", "scenario_en": man.get("description_en") or "",
            "default_theme": "custom", "outputs": ["web", "email"] if "email.html" in files else ["web"],
            "schema_obj": schema, "page": files["page.html"], "email": files.get("email.html"),
            "example": str(d / "example.json") if (d / "example.json").is_file() else None}


def discover() -> list[dict]:
    root = templates_root()
    out = []
    if root.is_dir():
        for d in sorted(root.iterdir()):
            if (d / "template.json").is_file():
                try:
                    out.append(load(d))
                except TemplateError as e:
                    out.append({"id": d.name, "ref": d.name, "custom": True, "dir": str(d), "broken": str(e)})
    return out


# ---------------------------------------------------------------- 模板语言
def parse(text: str) -> list:
    """→ 节点树：str | ("var", path, filter) | ("each"|"if"|"unless", path, children)。"""
    root: list = []
    stack = [("root", None, root)]
    pos = 0
    for m in _TOKEN.finditer(text):
        if m.start() > pos:
            stack[-1][2].append(text[pos:m.start()])
        pos = m.end()
        sigil, block, path, flt = m.group(1), m.group(2), m.group(3).strip(), (m.group(4) or "").lower()
        if sigil == "#":
            if not block:
                raise TemplateError(f"{{{{#{path}}}}} 只能是 #each / #if / #unless")
            node = (block, path, [])
            stack[-1][2].append(node)
            stack.append(node)
        elif sigil == "/":
            if len(stack) == 1 or stack[-1][0] != (block or path):
                raise TemplateError(f"{{{{/{block or path}}}}} 没有对应的开头")
            stack.pop()
        elif sigil == "^":
            raise TemplateError("不支持 {{^…}}，用 {{#unless …}}")
        else:
            if block:
                raise TemplateError(f"{{{{{block} …}}}} 前面要加 #")
            if flt and flt not in ("md", "paras", "url"):
                raise TemplateError(f"不认识的过滤器 | {flt}（只有 md / paras / url）")
            if not re.fullmatch(r"\.|@index|[\w-]+(\.[\w-]+)*", path):
                raise TemplateError(f"{{{{ {path} }}}} 写法不对")
            stack[-1][2].append(("var", path, flt))
    if len(stack) > 1:
        raise TemplateError(f"{{{{#{stack[-1][0]} {stack[-1][1]}}}}} 没有结束 {{{{/{stack[-1][0]}}}}}")
    if pos < len(text):
        root.append(text[pos:])
    return root


def _lookup(scopes: list, path: str):
    if path == ".":
        return scopes[-1][0]
    if path == "@index":
        return scopes[-1][1]
    parts = path.split(".")
    for scope, _ in reversed(scopes):
        cur, ok = scope, True
        for p in parts:
            if isinstance(cur, dict) and p in cur:
                cur = cur[p]
            elif isinstance(cur, list) and p.isdigit() and int(p) < len(cur):
                cur = cur[int(p)]
            else:
                ok = False
                break
        if ok:
            return cur
    return None


def _text(v) -> str:
    if v is None or isinstance(v, (dict, list)):
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _emit(nodes, scopes, out):
    for n in nodes:
        if isinstance(n, str):
            out.append(n)
        elif n[0] == "var":
            v = _text(_lookup(scopes, n[1]))
            out.append({"md": inline, "paras": paras, "url": lambda x: esc(safe_url(x))}.get(n[2], esc)(v))
        elif n[0] == "each":
            v = _lookup(scopes, n[1])
            for i, item in enumerate(v if isinstance(v, list) else [], 1):
                _emit(n[2], scopes + [(item, i)], out)
        else:
            v = _lookup(scopes, n[1])
            if bool(v) == (n[0] == "if"):
                _emit(n[2], scopes, out)


def fill(text: str, data: dict) -> str:
    out: list[str] = []
    _emit(parse(text), [(data, 0)], out)
    return "".join(out)


def check(data, t: dict) -> list[str]:
    return validate(data, t["schema_obj"])


def render(data: dict, t: dict, *, email: bool) -> str:
    src = t["email"] if email else t["page"]
    if email and not src:
        raise TemplateError(f"模板 {t['id']} 只有网页版（目录里没有 email.html）")
    html = fill(src, data)
    if not email:   # 网页加 CSP；邮件客户端不认 CSP，靠上面的静态检查
        meta = f'<meta http-equiv="Content-Security-Policy" content="{esc(CSP)}"><meta name="robots" content="noindex,nofollow">'
        html = re.sub(r"(<head[^>]*>)", r"\1" + meta.replace("\\", "\\\\"), html, count=1, flags=re.I) if re.search(r"<head[^>]*>", html, re.I) \
            else f"<!doctype html><html><head><meta charset=\"utf-8\">{meta}</head><body>{html}</body></html>"
    return html


# ---------------------------------------------------------------- 脚手架
STARTER_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema", "title": "my-template", "type": "object", "additionalProperties": False,
    "required": ["title", "items"],
    "properties": {
        "title": {"type": "string", "minLength": 1, "maxLength": 120},
        "date": {"type": "string", "maxLength": 40},
        "summary": {"type": "string", "maxLength": 600},
        "items": {"type": "array", "minItems": 1, "maxItems": 30, "items": {
            "type": "object", "additionalProperties": False, "required": ["title"],
            "properties": {"title": {"type": "string", "minLength": 1, "maxLength": 200},
                           "text": {"type": "string", "maxLength": 4000},
                           "url": {"type": "string", "pattern": "^(https://|mailto:)\\S+$"}}}},
    },
}
STARTER_EXAMPLE = {"title": "我的每日简报", "date": "2026-10-17", "summary": "这是一份示例：模型只填 JSON，“格式”由模板负责。",
                   "items": [{"title": "第一条", "text": "支持 **粗体**、`代码` 和 [链接](https://example.com)。", "url": "https://example.com"},
                             {"title": "第二条", "text": "- 列表一\n- 列表二"}]}
STARTER_PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ title }}</title>
<style>
body{margin:0;background:#2c2b26;color:#faf9f5;font:16px/1.75 "Ubuntu","Lexend","PingFang SC","Noto Sans CJK SC",system-ui,sans-serif}
main{max-width:760px;margin:0 auto;padding:48px 22px}h1{font-size:34px;line-height:1.25;margin:0 0 8px}
.meta{color:#acacac;font-size:14px}.tldr{border-left:3px solid #d97757;background:#141414;border-radius:12px;padding:12px 16px;margin:20px 0}
ol li::marker{color:#fcbe7c;font-weight:700}ul li::marker{color:#d97757}a{color:#71bbde}.wr-dq{color:#d97757}.wr-sq{color:#ffe292}
code{background:#141414;color:#b7e396;padding:1px 5px;border-radius:4px}
</style></head>
<body><main>
<h1>{{ title }}</h1>
{{#if date}}<p class="meta">{{ date }}</p>{{/if}}
{{#if summary}}<div class="tldr">{{ summary | md }}</div>{{/if}}
<ol>
{{#each items}}<li><strong>{{#if url}}<a href="{{ url | url }}">{{ title }}</a>{{/if}}{{#unless url}}{{ title }}{{/unless}}</strong>{{ text | paras }}</li>
{{/each}}</ol>
</main></body></html>
"""
STARTER_EMAIL = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark"><title>{{ title }}</title></head>
<body style="margin:0;padding:0;background:#2c2b26">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#2c2b26"><tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="640" cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:640px"><tr>
<td style="padding:8px 12px;font-family:Ubuntu,'PingFang SC',sans-serif;color:#faf9f5">
<h1 style="margin:0 0 8px;font-size:26px;line-height:1.3;color:#faf9f5">{{ title }}</h1>
{{#if date}}<p style="margin:0 0 14px;font-size:13px;color:#acacac">{{ date }}</p>{{/if}}
{{#if summary}}<p style="margin:0 0 18px;padding:12px 14px;border-left:3px solid #d97757;background:#141414;font-size:15px;line-height:1.7;color:#faf9f5">{{ summary }}</p>{{/if}}
{{#each items}}<p style="margin:0 0 4px;font-size:15px;line-height:1.6;color:#faf9f5"><span style="color:#fcbe7c;font-weight:700">{{ @index }}.</span>
<strong>{{#if url}}<a href="{{ url | url }}" style="color:#71bbde">{{ title }}</a>{{/if}}{{#unless url}}{{ title }}{{/unless}}</strong></p>
<p style="margin:0 0 14px 18px;font-size:14px;line-height:1.7;color:#acacac">{{ text }}</p>
{{/each}}</td></tr></table></td></tr></table></body></html>
"""


def scaffold(dest, tid: str | None = None) -> Path:
    d = Path(dest).expanduser()
    if d.exists() and any(d.iterdir()):
        raise TemplateError(f"{dest} 已存在且不是空目录（不覆盖）")
    tid = tid or re.sub(r"[^a-z0-9-]+", "-", d.name.lower()).strip("-") or "my-template"
    if not _ID.fullmatch(tid):
        raise TemplateError("模板 id 只能是小写字母数字和连字符（2–41 位）")
    d.mkdir(parents=True, exist_ok=True)
    man = {"schema": MANIFEST, "id": tid, "name_zh": "我的模板", "name_en": "My template",
           "description_zh": "一句话说明这个模板适合什么内容。", "description_en": "One line on what this template is for."}
    (d / "template.json").write_text(json.dumps(man, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (d / "schema.json").write_text(json.dumps({**STARTER_SCHEMA, "title": tid}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (d / "example.json").write_text(json.dumps(STARTER_EXAMPLE, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (d / "page.html").write_text(STARTER_PAGE, encoding="utf-8")
    (d / "email.html").write_text(STARTER_EMAIL, encoding="utf-8")
    load(d)   # 生成的脚手架本身必须过检查
    return d
