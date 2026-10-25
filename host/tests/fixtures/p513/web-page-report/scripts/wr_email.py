"""邮件安全版渲染（--email）：table 排版 + 全部内联样式、最宽 640px、无脚本无外链资源。

Gmail 会丢 <style> 和 CSS 变量、Outlook 不认 flex/grid，所以这里每个元素自己带 style，颜色写成调色板 hex
（来自主题，和网页版同一套）。所有用户文字先经 wr_render 的 esc / inline 转义，模板里不出现裸用户 HTML。
图片只放 https 外链（本地文件进不了邮件正文）。有邮件版的模板见 wr_schemas.EMAIL_TEMPLATES。
"""
from __future__ import annotations

import re

from wr_render import FONTS, THEMES, color, esc, inline, safe_url

MAX_W = 640


def palette(theme: str) -> dict:
    t = THEMES[theme]
    p = {k: color(t[k]) for k in ("bg", "surface", "line", "text", "muted", "accent", "accent2", "warn")}
    p["dark"] = bool(t.get("dark", theme in ("terminal", "ink")))
    p["ink"] = color(t.get("accent_ink", t["accent"]))
    if theme == "noir":   # Noir 的符号色：序号 Buttery、单引号 Banana Republic、行内代码 Sage Sensation、细线 Sooty
        p.update(idx=color("buttery"), sq=color("banana-republic"), code=color("sage-sensation"), line=color("sooty"))
    else:
        p.update(idx=p["ink"], sq=p["ink"], code=p["text"])
    p["link"] = p["accent2"] if p["dark"] else p["ink"]
    return p


class Mail:
    def __init__(self, theme: str):
        self.p = p = palette(theme)
        self.font = FONTS["sans"].replace('"', "'")
        self.mono = FONTS["mono"].replace('"', "'")
        self.st_p = f"margin:0 0 12px;font-size:15px;line-height:1.7;color:{p['text']}"

    # ---------------------------------------------------------------- 行内与段落
    def inline(self, text) -> str:
        p, s = self.p, inline(text)
        s = s.replace("<code>", f'<code style="font-family:{self.mono};font-size:13px;background:{p["surface"]};color:{p["code"]};'
                                f'padding:1px 5px;border-radius:4px">')
        s = s.replace("<strong>", f'<strong style="font-weight:700;color:{p["text"]}">')
        s = s.replace('<span class="wr-dq">', f'<span style="color:{p["accent"]}">').replace('<span class="wr-sq">', f'<span style="color:{p["sq"]}">')
        return re.sub(r'<a href="', f'<a style="color:{p["link"]};text-decoration:underline" href="', s)

    def link(self, url, label) -> str:
        return f'<a href="{esc(safe_url(url))}" style="color:{self.p["link"]};text-decoration:underline">{esc(label)}</a>'

    def bullets(self, rows: list[str]) -> str:
        p = self.p
        tr = "".join(f'<tr><td width="18" valign="top" style="color:{p["accent"]};font-size:15px;line-height:1.7;padding:0 0 6px">&#8226;</td>'
                     f'<td valign="top" style="font-size:15px;line-height:1.7;color:{p["text"]};padding:0 0 6px">{r}</td></tr>' for r in rows)
        return f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:0 0 12px">{tr}</table>'

    def numbered(self, rows: list[str]) -> str:
        p = self.p
        tr = "".join(f'<tr><td width="26" valign="top" style="color:{p["idx"]};font-weight:700;font-size:15px;line-height:1.7;padding:0 0 10px">{i}.</td>'
                     f'<td valign="top" style="font-size:15px;line-height:1.7;color:{p["text"]};padding:0 0 10px">{r}</td></tr>'
                     for i, r in enumerate(rows, 1))
        return f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:0 0 12px">{tr}</table>'

    def paras(self, text) -> str:
        out = []
        for block in re.split(r"\n\s*\n", (text or "").strip()):
            lines = [l for l in block.strip().split("\n") if l.strip()]
            if not lines:
                continue
            if all(l.lstrip().startswith(("- ", "* ")) for l in lines):
                out.append(self.bullets([self.inline(l.lstrip()[2:]) for l in lines]))
            else:
                out.append(f'<p style="{self.st_p}">' + "<br>".join(self.inline(l) for l in lines) + "</p>")
        return "".join(out)

    # ---------------------------------------------------------------- 块
    def h2(self, text, n=None) -> str:
        p = self.p
        num = f'<span style="font-family:{self.mono};font-size:13px;color:{p["accent"]};padding-right:10px">{n:02d}</span>' if n else ""
        return f'<h2 style="margin:30px 0 12px;font-size:20px;line-height:1.35;font-weight:700;color:{p["text"]}">{num}{self.inline(text)}</h2>'

    def eyebrow(self, text, colour=None) -> str:
        return (f'<div style="font-family:{self.mono};font-size:12px;letter-spacing:1.5px;text-transform:uppercase;'
                f'color:{colour or self.p["accent"]};margin:0 0 8px">{text}</div>')

    def box(self, inner, edge=None) -> str:
        p = self.p
        return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:0 0 14px">'
                f'<tr><td style="background:{p["surface"]};border:1px solid {p["line"]};border-left:3px solid {edge or p["line"]};'
                f'border-radius:10px;padding:14px 16px">{inner}</td></tr></table>')

    def quote(self, text, cite=None) -> str:
        p = self.p
        c = f'<div style="font-size:13px;color:{p["muted"]};margin-top:6px">— {esc(cite)}</div>' if cite else ""
        return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:6px 0 16px">'
                f'<tr><td style="border-left:3px solid {p["accent"]};padding:4px 0 4px 16px;font-size:18px;line-height:1.6;'
                f'font-style:italic;color:{p["text"]}"><span style="color:{p["accent"]}">&#8220;</span>{self.inline(text)}{c}</td></tr></table>')

    def code(self, c) -> str:
        if not c or not c.get("content"):
            return ""
        p = self.p
        lang = f'<div style="font-family:{self.mono};font-size:11px;color:{p["muted"]};margin:0 0 6px">{esc(c.get("lang"))}</div>' if c.get("lang") else ""
        return (f'{lang}<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:0 0 14px">'
                f'<tr><td style="background:{p["surface"]};border:1px solid {p["line"]};border-radius:8px;padding:12px 14px">'
                f'<pre style="margin:0;font-family:{self.mono};font-size:13px;line-height:1.55;color:{p["text"]};white-space:pre-wrap;'
                f'word-break:break-all">{esc(c["content"])}</pre></td></tr></table>')

    def kpis(self, items) -> str:
        p, rows = self.p, []
        for i in range(0, len(items), 3):
            cells = []
            for k in items[i:i + 3]:
                trend = k.get("trend")
                tc = {"up": color("sage-sensation") if p["dark"] else color("emerald-oasis"), "down": p["accent"]}.get(trend, p["muted"])
                arrow = {"up": "&#9650; ", "down": "&#9660; ", "flat": "&#9644; "}.get(trend or "", "")
                delta = f'<div style="font-family:{self.mono};font-size:12px;color:{tc}">{arrow}{esc(k["delta"])}</div>' if k.get("delta") else ""
                note = f'<div style="font-size:12px;color:{p["muted"]}">{esc(k["note"])}</div>' if k.get("note") else ""
                cells.append(f'<td width="33%" valign="top" style="padding:4px"><div style="background:{p["surface"]};border:1px solid {p["line"]};'
                             f'border-radius:12px;padding:12px 12px"><div style="font-size:12px;color:{p["muted"]}">{esc(k.get("label"))}</div>'
                             f'<div style="font-size:22px;line-height:1.25;font-weight:700;color:{p["text"]};margin:4px 0 2px">{esc(k.get("value"))}</div>'
                             f'{delta}{note}</div></td>')
            cells += ['<td width="33%"></td>'] * (3 - len(cells))
            rows.append("<tr>" + "".join(cells) + "</tr>")
        return f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:4px -4px 12px">{"".join(rows)}</table>'

    def hero(self, d, eyebrow) -> str:
        p = self.p
        out = [self.eyebrow(esc(eyebrow)),
               f'<h1 style="margin:0 0 10px;font-size:28px;line-height:1.25;font-weight:700;color:{p["text"]}">{self.inline(d.get("title"))}</h1>']
        if d.get("subtitle"):
            out.append(f'<p style="margin:0 0 10px;font-size:16px;line-height:1.6;color:{p["muted"]}">{self.inline(d["subtitle"])}</p>')
        meta = " · ".join(esc(x) for x in (d.get("author"), d.get("date")) if x)
        if meta:
            out.append(f'<p style="margin:0 0 14px;font-size:13px;color:{p["muted"]}">{meta}</p>')
        if d.get("cover_image") and str(d["cover_image"]).startswith("https://"):
            out.append(f'<img src="{esc(safe_url(d["cover_image"]))}" width="592" alt="" style="display:block;width:100%;max-width:592px;height:auto;border-radius:10px;margin:0 0 14px">')
        if d.get("summary"):
            out.append(self.box(f'<div style="font-family:{self.mono};font-size:11px;letter-spacing:1.5px;color:{p["accent"]};margin:0 0 4px">TL;DR</div>'
                                f'<div style="font-size:15px;line-height:1.7;color:{p["text"]}">{self.inline(d["summary"])}</div>', p["accent"]))
        return "".join(out)

    def sources(self, srcs, heading) -> str:
        if not srcs:
            return ""
        rows = []
        for x in srcs:
            t = self.link(x["url"], x.get("title")) if x.get("url") else esc(x.get("title"))
            extra = "".join(f" · {esc(x[k])}" for k in ("publisher", "date") if x.get(k))
            rows.append(f'<span style="font-size:13px">{t}<span style="color:{self.p["muted"]}">{extra}</span></span>')
        return self.h2(heading) + self.numbered(rows)

    def shell(self, d, inner, template_id, lang) -> str:
        p = self.p
        scheme = "dark" if p["dark"] else "light"
        pre = esc((d.get("summary") or d.get("subtitle") or "")[:180])
        foot = self.inline(d.get("footer_note")) if d.get("footer_note") else esc(d.get("author") or "")
        return ("<!doctype html>\n"
                f'<html lang="{esc(lang)}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                f'<meta name="color-scheme" content="{scheme}"><meta name="supported-color-schemes" content="{scheme}">'
                f'<meta name="generator" content="web-page-report {esc(template_id)} email"><title>{esc(d.get("title"))}</title></head>'
                f'<body style="margin:0;padding:0;background:{p["bg"]};color:{p["text"]};font-family:{self.font}">'
                f'<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:{p["bg"]}">{pre}</div>'
                f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{p["bg"]}" style="background:{p["bg"]}">'
                f'<tr><td align="center" style="padding:24px 12px">'
                f'<table role="presentation" width="{MAX_W}" cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:{MAX_W}px">'
                f'<tr><td style="padding:8px 12px 8px;font-family:{self.font};color:{p["text"]}">{inner}'
                f'<div style="margin-top:32px;padding-top:14px;border-top:1px solid {p["line"]};font-size:12px;color:{p["muted"]}">'
                f'{foot}{" · " if foot else ""}网页报告 · {esc(template_id)}</div>'
                "</td></tr></table></td></tr></table></body></html>\n")


# ---------------------------------------------------------------- 各模板的邮件版
def e15(d, m: Mail) -> str:
    out = [m.hero(d, d.get("eyebrow") or "AGENT J · BRIEF")]
    if d.get("highlights"):
        out.append(m.bullets([m.inline(x) for x in d["highlights"]]))
    if d.get("kpis"):
        out.append(m.kpis(d["kpis"]))
    for i, sec in enumerate(d.get("sections") or [], 1):
        out.append(m.h2(sec.get("heading"), i) + m.paras(sec.get("body")))
        if sec.get("items"):
            rows = []
            for x in sec["items"]:
                t = m.inline(x.get("title"))
                if x.get("url"):
                    t = f'<a href="{esc(safe_url(x["url"]))}" style="color:{m.p["link"]};text-decoration:underline">{t}</a>'
                meta = f' <span style="font-family:{m.mono};font-size:12px;color:{m.p["muted"]}">{esc(x["meta"])}</span>' if x.get("meta") else ""
                body = m.paras(x.get("text")).replace(f"color:{m.p['text']}", f"color:{m.p['muted']}").replace("margin:0 0 12px", "margin:4px 0 0")
                rows.append(f'<div style="font-weight:700">{t}{meta}</div>{body}')
            out.append(m.numbered(rows))
        if sec.get("quote"):
            out.append(m.quote(sec["quote"].get("text"), sec["quote"].get("cite")))
        if sec.get("links"):
            out.append(m.bullets([m.link(l.get("url"), l.get("label")) for l in sec["links"]]))
    if d.get("closing"):
        out.append('<div style="margin-top:26px">' + m.paras(d["closing"]) + "</div>")
    out.append(m.sources(d.get("sources"), "来源"))
    return "".join(out)


def e02(d, m: Mail) -> str:
    p = m.p
    out = [m.hero(d, "$ cat tech-share.md")]
    if d.get("stack"):
        out.append('<p style="margin:0 0 12px">' + "".join(
            f'<span style="display:inline-block;border:1px solid {p["line"]};border-radius:999px;padding:1px 10px;margin:0 6px 6px 0;'
            f'font-size:12px;color:{p["text"]}">{esc(x)}</span>' for x in d["stack"]) + "</p>")
    for sec in d.get("sections") or []:
        out.append(m.h2(sec.get("heading")).replace('">', f'"><span style="color:{p["accent"]}">## </span>', 1)
                   + m.paras(sec.get("body")) + m.code(sec.get("code")))
        c = sec.get("callout")
        if c:
            edge = {"warn": p["warn"], "tip": p["accent"]}.get(c.get("kind"), p["accent2"])
            label = {"note": "注意", "tip": "提示", "warn": "警告"}.get(c.get("kind", "note"), "注意")
            out.append(m.box(f'<b style="color:{edge}">{label}</b> <span style="font-size:15px;color:{p["text"]}">{m.inline(c.get("text"))}</span>', edge))
    if d.get("commands"):
        rows = "".join(f'<tr><td valign="top" style="padding:8px 10px 8px 0;border-bottom:1px solid {p["line"]}"><code style="font-family:{m.mono};'
                       f'font-size:13px;color:{p["accent"]}">{esc(c.get("cmd"))}</code></td><td valign="top" style="padding:8px 0;font-size:14px;'
                       f'border-bottom:1px solid {p["line"]};color:{p["text"]}">{m.inline(c.get("desc"))}</td></tr>' for c in d["commands"])
        out.append(m.h2("命令速查") + f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{rows}</table>')
    if d.get("links"):
        out.append(m.h2("参考链接") + m.bullets([m.link(l.get("url"), l.get("label")) for l in d["links"]]))
    return "".join(out)


def e14(d, m: Mail) -> str:
    p = m.p
    out = [m.hero(d, "RESEARCH · 调研")]
    if d.get("question"):
        out.append(m.box(f'<b style="color:{p["accent2"]}">研究问题</b> <span style="font-size:15px;color:{p["text"]}">{m.inline(d["question"])}</span>', p["accent2"]))
    if d.get("method"):
        out.append(m.h2("方法") + m.paras(d["method"]))
    conf = {"high": ("高置信", color("emerald-oasis")), "medium": ("中置信", p["muted"]), "low": ("低置信", p["accent"])}
    srcs = d.get("sources") or []
    if d.get("findings"):
        out.append(m.h2("主要发现"))
        for i, f in enumerate(d["findings"], 1):
            label, c = conf.get(f.get("confidence", "medium"), conf["medium"])
            refs = "".join(f"[{int(n)}]" for n in f.get("sources") or [] if 1 <= int(n) <= len(srcs))
            out.append(m.box(m.eyebrow(f'发现 {i:02d} · <span style="color:{c}">{label}</span>', p["muted"])
                             + f'<div style="font-size:17px;font-weight:700;line-height:1.45;color:{p["text"]};margin:0 0 6px">{m.inline(f.get("claim"))}'
                               f' <sup style="font-size:11px;color:{p["muted"]}">{refs}</sup></div>' + m.paras(f.get("evidence"))))
    if d.get("conclusion"):
        out.append(m.h2("结论") + m.paras(d["conclusion"]))
    if d.get("limitations"):
        out.append(m.h2("局限与未验证") + m.paras(d["limitations"]))
    out.append(m.sources(srcs, "来源"))
    return "".join(out)


EMAIL_RENDERERS = {"R15": e15, "R02": e02, "R14": e14}


def render_email(data: dict, template: dict) -> str:
    theme = data.get("theme") or template["default_theme"]
    if theme not in THEMES:
        theme = template["default_theme"]
    m = Mail(theme)
    inner = EMAIL_RENDERERS[template["id"]](data, m)
    return m.shell(data, inner, template["id"], data.get("lang") or "zh-CN")
