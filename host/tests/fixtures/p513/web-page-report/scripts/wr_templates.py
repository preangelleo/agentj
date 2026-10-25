"""十五个模板的正文渲染器。每个渲染器: (data, ctx) -> (body_html, sections)。

sections = [{id, heading, text}]，同时用于目录、WebMCP 工具与 report.json。
所有字符串都经 wr_render 的 esc / inline / paras 转义，模板里不出现裸用户 HTML。
"""
from __future__ import annotations

import re
from pathlib import Path

from wr_render import esc, hero, inline, paras, safe_url, slug, toc, flatten_text

REG_PATH = Path(__file__).resolve().parent.parent / "templates" / "registry.json"


class Ctx:
    """收集本地媒体文件：data 里的相对路径 → 页面里的 assets/<名字>。"""

    def __init__(self, base_dir: Path | None):
        self.base = base_dir
        self.assets: dict[str, Path] = {}
        self.frames = False

    def media(self, src) -> str:
        src = str(src or "").strip()
        if src.startswith("https://"):
            return safe_url(src)
        if not src or ":" in src or src.startswith(("/", "~")) or ".." in Path(src).parts:
            return "#"
        p = (self.base / src) if self.base else Path(src)
        name = re.sub(r"[^\w.\-]", "_", Path(src).name)
        if name.startswith("_wr-"):  # 保留给本包自带的资源（如 WebMCP 脚本）
            name = "u" + name
        stem, dot, ext = name.rpartition(".")
        key = name
        n = 1
        while key in self.assets and self.assets[key] != p:
            n += 1
            key = f"{stem}-{n}{dot}{ext}" if dot else f"{name}-{n}"
        self.assets[key] = p
        return f"assets/{key}"


class Sec:
    def __init__(self):
        self.items: list[dict] = []

    def add(self, heading, value, prefix="") -> str:
        sid = slug(prefix or heading, len(self.items) + 1)
        self.items.append({"id": sid, "heading": str(heading), "text": flatten_text(value).strip()})
        return sid


def h2(sid, text):
    return f'<h2 id="{esc(sid)}">{inline(text)}</h2>'


def code_block(c):
    if not c:
        return ""
    lang = esc(c.get("lang") or "")
    label = f'<div class="wr-eyebrow" style="margin:0 0 6px">{lang}</div>' if lang else ""
    return f'{label}<pre><code>{esc(c.get("content"))}</code></pre>'


def figure(ctx, img, cls=""):
    if not img or not img.get("src"):
        return ""
    cap = f"<figcaption>{inline(img.get('caption'))}</figcaption>" if img.get("caption") else ""
    alt = esc(img.get("alt") or img.get("caption") or "")
    return f'<figure class="{cls}"><img src="{esc(ctx.media(img["src"]))}" alt="{alt}" loading="lazy">{cap}</figure>'


def callout(c):
    if not c:
        return ""
    kind = c.get("kind", "note")
    label = {"note": "注意", "tip": "提示", "warn": "警告"}.get(kind, "注意")
    return f'<div class="wr-callout {esc(kind)}"><b>{label}</b> {inline(c.get("text"))}</div>'


def ul(items, cls=""):
    if not items:
        return ""
    return f'<ul class="{cls}">' + "".join(f"<li>{inline(i)}</li>" for i in items) + "</ul>"


STATUS = {"todo": "待办", "doing": "进行中", "done": "已完成", "blocked": "受阻"}


# ---------------------------------------------------------------- R01 会议纪要 / 日报
def r01(d, ctx):
    s, out = Sec(), [hero(d, "BRIEFING · 纪要")]
    body = []
    if d.get("attendees"):
        sid = s.add("参会人", [f"{a.get('name')} {a.get('role') or ''}" for a in d["attendees"]])
        body.append(h2(sid, "参会人") + "<p>" + "".join(
            f'<span class="wr-tag">{esc(a.get("name"))}{" · " + esc(a["role"]) if a.get("role") else ""}</span>' for a in d["attendees"]) + "</p>")
    if d.get("context"):
        sid = s.add("背景", d["context"])
        body.append(h2(sid, "背景") + paras(d["context"]))
    if d.get("decisions"):
        sid = s.add("决议", d["decisions"])
        body.append(h2(sid, "决议") + '<ol>' + "".join(
            f'<li>{inline(x.get("text"))}{" <span class=wr-pill>" + esc(x["owner"]) + "</span>" if x.get("owner") else ""}</li>'
            for x in d["decisions"]) + "</ol>")
    if d.get("action_items"):
        sid = s.add("行动项", d["action_items"])
        rows = "".join(f'<tr><td>{inline(a.get("task"))}</td><td>{esc(a.get("owner") or "—")}</td><td>{esc(a.get("due") or "—")}</td>'
                       f'<td><span class="wr-pill">{STATUS.get(a.get("status", "todo"), "待办")}</span></td></tr>' for a in d["action_items"])
        body.append(h2(sid, "行动项") + f"<table><thead><tr><th>事项</th><th>负责人</th><th>截止</th><th>状态</th></tr></thead><tbody>{rows}</tbody></table>")
    for t in d.get("discussion") or []:
        sid = s.add(t.get("topic"), t.get("notes"))
        body.append(h2(sid, t.get("topic")) + paras(t.get("notes")))
    if d.get("open_questions"):
        sid = s.add("待定问题", d["open_questions"])
        body.append(h2(sid, "待定问题") + ul(d["open_questions"]))
    if d.get("next_meeting"):
        sid = s.add("下次会议", d["next_meeting"])
        body.append(f'<div class="wr-callout tip" id="{sid}"><b>下次会议</b> {inline(d["next_meeting"])}</div>')
    return "".join(out) + toc(s.items) + "".join(body), s.items


# ---------------------------------------------------------------- R02 技术分享（黑客风）
def r02(d, ctx):
    s = Sec()
    head = [hero(d, "$ cat tech-share.md")]
    if d.get("stack"):
        head.append('<p>' + "".join(f'<span class="wr-tag">{esc(x)}</span>' for x in d["stack"]) + "</p>")
    body = []
    for sec in d.get("sections") or []:
        sid = s.add(sec.get("heading"), [sec.get("body"), (sec.get("code") or {}).get("content")])
        body.append(h2(sid, sec.get("heading")) + paras(sec.get("body")) + code_block(sec.get("code")) + callout(sec.get("callout")))
    if d.get("commands"):
        sid = s.add("命令速查", [f"{c.get('cmd')} {c.get('desc') or ''}" for c in d["commands"]])
        rows = "".join(f'<tr><td><code>{esc(c.get("cmd"))}</code></td><td>{inline(c.get("desc"))}</td></tr>' for c in d["commands"])
        body.append(h2(sid, "命令速查") + f"<table><tbody>{rows}</tbody></table>")
    if d.get("links"):
        sid = s.add("参考链接", [l.get("label") for l in d["links"]])
        body.append(h2(sid, "参考链接") + "<ul>" + "".join(
            f'<li><a href="{esc(safe_url(l.get("url")))}" rel="noopener noreferrer">{esc(l.get("label"))}</a></li>' for l in d["links"]) + "</ul>")
    # 终端味的前缀放在 CSS 里，不进标题文字（WebMCP / 目录拿到的是干净标题；主题可覆盖前缀）
    css = ".wr-hero h1::before{content:'> ';color:var(--wr-accent-ink)}h2{color:var(--wr-accent-ink)}h2::before{content:'## ';opacity:.7}"
    return "".join(head) + toc(s.items) + "".join(body), s.items, css


# ---------------------------------------------------------------- R03 商业 BP
def r03(d, ctx):
    s, out = Sec(), []
    tag = f'<p class="wr-sub" style="font-size:24px">{inline(d.get("tagline"))}</p>' if d.get("tagline") else ""
    out.append(f'<section class="bp-slide bp-cover"><div class="wr-eyebrow">PITCH · 商业计划</div><h1>{inline(d.get("title"))}</h1>{tag}'
               f'<div class="wr-meta"><span>{esc(d.get("author") or "")}</span><span>{esc(d.get("date") or "")}</span></div></section>')

    def slide(key, heading, inner):
        sid = s.add(heading, d.get(key))
        out.append(f'<section class="bp-slide" id="{sid}"><div class="wr-eyebrow">{len(s.items):02d}</div><h2>{esc(heading)}</h2>{inner}</section>')

    if d.get("problem"):
        slide("problem", "问题", paras(d["problem"], "bp-big"))
    if d.get("solution"):
        slide("solution", "方案", paras(d["solution"], "bp-big"))
    if d.get("market"):
        m = d["market"]
        cards = "".join(f'<div class="wr-card wr-kpi"><div class="v">{esc(m[k])}</div><div class="l">{k.upper()}</div></div>' for k in ("tam", "sam", "som") if m.get(k))
        slide("market", "市场", f'<div class="wr-grid">{cards}</div>' + paras(m.get("note")))
    if d.get("traction"):
        cards = "".join(f'<div class="wr-card wr-kpi"><div class="v">{esc(t.get("value"))}</div><div class="l">{esc(t.get("label"))}</div>'
                        f'{"<div class=d>" + esc(t["delta"]) + "</div>" if t.get("delta") else ""}</div>' for t in d["traction"])
        slide("traction", "进展与数据", f'<div class="wr-grid">{cards}</div>')
    if d.get("business_model"):
        slide("business_model", "商业模式", paras(d["business_model"]))
    if d.get("competition"):
        rows = "".join(f'<tr><td>{esc(c.get("name"))}</td><td>{inline(c.get("diff"))}</td></tr>' for c in d["competition"])
        slide("competition", "竞争格局", f"<table><thead><tr><th>对手</th><th>我们的差异</th></tr></thead><tbody>{rows}</tbody></table>")
    if d.get("team"):
        cards = "".join(f'<div class="wr-card"><h3>{esc(p.get("name"))}</h3><div class="wr-pill">{esc(p.get("role"))}</div>{paras(p.get("bio"))}</div>' for p in d["team"])
        slide("team", "团队", f'<div class="wr-grid">{cards}</div>')
    if d.get("roadmap"):
        items = "".join(f'<li><b>{esc(r.get("when"))}</b> — {inline(r.get("what"))}</li>' for r in d["roadmap"])
        slide("roadmap", "路线图", f"<ol>{items}</ol>")
    if d.get("ask"):
        a = d["ask"]
        bars = "".join(f'<div style="margin:8px 0"><div style="display:flex;justify-content:space-between"><span>{esc(u.get("item"))}</span><span>{esc(u.get("pct"))}%</span></div>'
                       f'<div style="height:8px;background:var(--wr-surface);border-radius:4px"><div style="height:8px;width:{max(0, min(100, int(u.get("pct") or 0)))}%;background:var(--wr-s{i % 8});border-radius:4px"></div></div></div>'
                       for i, u in enumerate(a.get("use_of_funds") or []))
        slide("ask", "融资需求", f'<div class="bp-ask">{esc(a.get("amount"))}</div>{paras(a.get("note"))}{bars}')
    if d.get("contact"):
        slide("contact", "联系我们", paras(d["contact"], "bp-big"))
    css = (".bp-slide{min-height:88vh;display:flex;flex-direction:column;justify-content:center;border-bottom:1px solid var(--wr-line);padding:48px 0}"
           ".bp-slide h2{font-size:clamp(28px,4vw,44px);margin-top:0}.bp-cover h1{font-size:clamp(40px,7vw,76px)}"
           ".bp-big{font-size:clamp(19px,2.2vw,24px);line-height:1.6}.bp-ask{font:700 clamp(40px,7vw,72px)/1.1 var(--wr-head);color:var(--wr-accent-ink);margin-bottom:12px}"
           "@media print{.bp-slide{min-height:auto;page-break-after:always}}")
    return "".join(out), s.items, css


# ---------------------------------------------------------------- R04 讲故事长文
def r04(d, ctx):
    s = Sec()
    out = [hero(d, "STORY · 故事")]
    if d.get("lede"):
        out.append(paras(d["lede"], "st-lede"))
    for i, ch in enumerate(d.get("chapters") or [], 1):
        sid = s.add(ch.get("heading"), [ch.get("body"), (ch.get("quote") or {}).get("text")])
        q = ch.get("quote")
        quote = f'<blockquote class="wr-quote">{inline(q.get("text"))}{"<cite>— " + esc(q["cite"]) + "</cite>" if q.get("cite") else ""}</blockquote>' if q else ""
        out.append(f'<section class="st-ch"><div class="wr-eyebrow">第 {i} 章</div>{h2(sid, ch.get("heading"))}'
                   f'{figure(ctx, ch.get("image"), "st-fig")}{paras(ch.get("body"))}{quote}</section>')
    if d.get("epilogue"):
        sid = s.add("尾声", d["epilogue"])
        out.append(h2(sid, "尾声") + paras(d["epilogue"]))
    css = (".wr-wrap{max-width:720px}body{font-family:var(--wr-head);font-size:18px;line-height:1.9}"
           ".st-lede{font-size:22px;line-height:1.7}.st-lede:first-of-type::first-letter{float:left;font-size:3.2em;line-height:.9;margin:6px 10px 0 0;color:var(--wr-accent-ink)}"
           ".st-ch{margin-top:56px}.st-fig img{width:100%}")
    return "".join(out), s.items, css


# ---------------------------------------------------------------- R05 图文并茂 / 作品集
def r05(d, ctx):
    s = Sec()
    out = [hero(d, "GALLERY · 图文")]
    if d.get("intro"):
        out.append(paras(d["intro"]))
    cards = []
    for it in d.get("items") or []:
        sid = s.add(it.get("title"), [it.get("caption"), it.get("description")])
        link = f'<a href="{esc(safe_url(it["link"]))}" rel="noopener noreferrer">查看 →</a>' if it.get("link") else ""
        cards.append(f'<article class="gl-item wr-card" id="{sid}"><img src="{esc(ctx.media(it.get("src")))}" alt="{esc(it.get("alt") or it.get("title"))}" loading="lazy">'
                     f'<h3>{inline(it.get("title"))}</h3>{"<p class=gl-cap>" + inline(it["caption"]) + "</p>" if it.get("caption") else ""}'
                     f'{paras(it.get("description"))}{link}</article>')
    layout = d.get("layout") or "grid"
    out.append(f'<div class="gl gl-{esc(layout)}">' + "".join(cards) + "</div>")
    css = (".gl-grid{display:grid;gap:20px;grid-template-columns:repeat(auto-fill,minmax(300px,1fr))}"
           ".gl-masonry{columns:3 300px;column-gap:20px}.gl-masonry .gl-item{break-inside:avoid;margin-bottom:20px}"
           ".gl-stack .gl-item{margin-bottom:28px}.gl-item{padding:12px}.gl-item img{width:100%;display:block;border-radius:10px;background:var(--wr-soft)}"
           ".gl-item h3{margin:12px 4px 4px}.gl-item p,.gl-item a{margin:4px}.gl-cap{color:var(--wr-muted);font-size:14px}")
    return "".join(out), s.items, css


def _timecode(t):
    m = re.fullmatch(r"(?:(\d+):)?(\d{1,2}):(\d{2})", str(t or ""))
    if not m:
        return 0
    h, mi, se = m.groups()
    return int(h or 0) * 3600 + int(mi) * 60 + int(se)


# ---------------------------------------------------------------- R06 播客 / 音频
def r06(d, ctx):
    s = Sec()
    a = d.get("audio") or {}
    cover = f'<img class="au-cover" src="{esc(ctx.media(d["cover"]))}" alt="">' if d.get("cover") else ""
    out = [hero(d, "AUDIO · 音频"),
           f'<div class="wr-card au-player">{cover}<div style="flex:1;min-width:240px"><h3>{inline(a.get("title") or d.get("title"))}</h3>'
           f'<div class="wr-meta">{esc(a.get("duration") or "")}</div>'
           f'<audio controls preload="metadata" src="{esc(ctx.media(a.get("src")))}" style="width:100%;margin-top:10px"></audio></div></div>']
    if d.get("guests"):
        out.append("<p>" + "".join(f'<span class="wr-tag">{esc(g)}</span>' for g in d["guests"]) + "</p>")
    if d.get("show_notes"):
        sid = s.add("节目笔记", d["show_notes"])
        out.append(h2(sid, "节目笔记") + paras(d["show_notes"]))
    if d.get("chapters"):
        sid = s.add("章节", [f"{c.get('t')} {c.get('title')}" for c in d["chapters"]])
        out.append(h2(sid, "章节") + '<table><tbody>' + "".join(
            f'<tr><td style="width:90px"><span class="wr-pill">{esc(c.get("t"))}</span></td><td>{inline(c.get("title"))}</td></tr>' for c in d["chapters"]) + "</tbody></table>")
    if d.get("transcript"):
        sid = s.add("文字稿", [f"{x.get('speaker') or ''}: {x.get('text')}" for x in d["transcript"]])
        out.append(h2(sid, "文字稿") + "".join(
            f'<p class="au-line"><span class="wr-pill">{esc(x.get("t") or "")}</span> <b>{esc(x.get("speaker") or "")}</b> {inline(x.get("text"))}</p>' for x in d["transcript"]))
    css = (".au-player{display:flex;gap:20px;align-items:center;flex-wrap:wrap}.au-cover{width:160px;height:160px;object-fit:cover;border-radius:12px}"
           ".au-line{margin:.5em 0}")
    return "".join(out), s.items, css


EMBED = {"youtube": "https://www.youtube-nocookie.com/embed/{id}", "bilibili": "https://player.bilibili.com/player.html?bvid={id}&autoplay=0",
         "vimeo": "https://player.vimeo.com/video/{id}"}


# ---------------------------------------------------------------- R07 视频为主
def r07(d, ctx):
    s = Sec()
    v = d.get("video") or {}
    if v.get("embed"):
        e = v["embed"]
        vid = re.sub(r"[^\w\-]", "", str(e.get("id") or ""))
        ctx.frames = True
        player = (f'<div class="vd-frame"><iframe src="{esc(EMBED[e.get("provider", "youtube")].format(id=vid))}" title="{esc(d.get("title"))}" '
                  'allow="encrypted-media; picture-in-picture; fullscreen" allowfullscreen loading="lazy" referrerpolicy="strict-origin-when-cross-origin"></iframe></div>')
    else:
        poster = f' poster="{esc(ctx.media(v["poster"]))}"' if v.get("poster") else ""
        player = f'<video class="vd-video" controls preload="metadata" playsinline src="{esc(ctx.media(v.get("src")))}"{poster}></video>'
    out = [hero(d, "VIDEO · 视频"), player]
    if d.get("key_points"):
        sid = s.add("要点", d["key_points"])
        out.append(h2(sid, "要点") + '<div class="wr-grid">' + "".join(
            f'<div class="wr-card"><div class="wr-eyebrow">{i:02d}</div>{inline(k)}</div>' for i, k in enumerate(d["key_points"], 1)) + "</div>")
    if d.get("chapters"):
        sid = s.add("章节", [f"{c.get('t')} {c.get('title')}" for c in d["chapters"]])
        out.append(h2(sid, "章节") + "<ol>" + "".join(f'<li><span class="wr-pill">{esc(c.get("t"))}</span> {inline(c.get("title"))}</li>' for c in d["chapters"]) + "</ol>")
    if d.get("transcript_excerpt"):
        sid = s.add("文字摘录", d["transcript_excerpt"])
        out.append(h2(sid, "文字摘录") + paras(d["transcript_excerpt"]))
    css = (".vd-video{width:100%;aspect-ratio:16/9;background:var(--wr-surface);display:block}"
           ".vd-frame{position:relative;aspect-ratio:16/9}.vd-frame iframe{position:absolute;inset:0;width:100%;height:100%;border:0}")
    return "".join(out), s.items, css


def svg_chart(ch):
    labels = [str(x) for x in ch.get("labels") or []]
    series = ch.get("series") or []
    vals = [v for se in series for v in se.get("values") or [] if isinstance(v, (int, float))]
    if not labels or not vals:
        return ""
    W, H, L, B, T = 640, 260, 48, 32, 14
    vmax = max(vals + [0]) or 1
    vmin = min(vals + [0])
    span = (vmax - vmin) or 1
    y = lambda v: T + (H - T - B) * (1 - (v - vmin) / span)
    parts = [f'<line x1="{L}" y1="{y(0):.1f}" x2="{W - 8}" y2="{y(0):.1f}" style="stroke:var(--wr-line)"/>']
    for k in range(5):
        gv = vmin + span * k / 4
        tick = esc(f"{round(gv, 2):g}")
        parts.append(f'<text x="{L - 6}" y="{y(gv) + 4:.1f}" text-anchor="end" style="fill:var(--wr-muted);font-size:11px">{tick}</text>')
    n = len(labels)
    step = (W - L - 8) / n
    kind = ch.get("type") or "bar"
    if kind == "line":
        for si, se in enumerate(series):
            pts = " ".join(f"{L + step * (i + .5):.1f},{y(v):.1f}" for i, v in enumerate(se.get("values") or []) if isinstance(v, (int, float)))
            parts.append(f'<polyline points="{pts}" style="fill:none;stroke:var(--wr-s{si % 8});stroke-width:2.5"/>')
            for i, v in enumerate(se.get("values") or []):
                if isinstance(v, (int, float)):
                    parts.append(f'<circle cx="{L + step * (i + .5):.1f}" cy="{y(v):.1f}" r="3.5" style="fill:var(--wr-s{si % 8})"><title>{esc(se.get("name"))} {esc(labels[i] if i < n else "")}: {esc(v)}</title></circle>')
    else:
        bw = step * .8 / max(1, len(series))
        for si, se in enumerate(series):
            for i, v in enumerate(se.get("values") or []):
                if not isinstance(v, (int, float)) or i >= n:
                    continue
                x = L + step * i + step * .1 + bw * si
                top, bot = sorted((y(v), y(0)))
                parts.append(f'<rect x="{x:.1f}" y="{top:.1f}" width="{bw:.1f}" height="{max(1, bot - top):.1f}" rx="3" style="fill:var(--wr-s{si % 8})"><title>{esc(se.get("name"))} {esc(labels[i])}: {esc(v)}</title></rect>')
    for i, lb in enumerate(labels):
        parts.append(f'<text x="{L + step * (i + .5):.1f}" y="{H - 10}" text-anchor="middle" style="fill:var(--wr-muted);font-size:11px">{esc(lb[:12])}</text>')
    legend = "".join(f'<span style="margin-right:14px"><i style="display:inline-block;width:10px;height:10px;border-radius:2px;background:var(--wr-s{i % 8});margin-right:5px"></i>{esc(se.get("name"))}</span>'
                     for i, se in enumerate(series)) if len(series) > 1 else ""
    unit = f' <span class="wr-pill">{esc(ch["unit"])}</span>' if ch.get("unit") else ""
    return (f'<figure class="wr-card"><h3>{inline(ch.get("title"))}{unit}</h3>'
            f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{esc(ch.get("title"))}" style="width:100%;height:auto">{"".join(parts)}</svg>'
            f'<figcaption>{legend}{inline(ch.get("note")) if ch.get("note") else ""}</figcaption></figure>')


# ---------------------------------------------------------------- R08 数据报表
def r08(d, ctx):
    s = Sec()
    out = [hero(d, "DATA · 数据报表")]
    if d.get("kpis"):
        sid = s.add("核心指标", [f"{k.get('label')} {k.get('value')} {k.get('delta') or ''}" for k in d["kpis"]])
        arrow = {"up": "▲", "down": "▼", "flat": "■"}
        out.append(f'<div class="wr-grid" id="{sid}">' + "".join(
            f'<div class="wr-card wr-kpi"><div class="l">{esc(k.get("label"))}</div><div class="v">{esc(k.get("value"))}</div>'
            f'{"<div class=" + chr(34) + "d " + esc(k.get("trend", "flat")) + chr(34) + ">" + arrow.get(k.get("trend", "flat"), "") + " " + esc(k["delta"]) + "</div>" if k.get("delta") else ""}'
            f'{"<div class=l>" + inline(k["note"]) + "</div>" if k.get("note") else ""}</div>' for k in d["kpis"]) + "</div>")
    for ch in d.get("charts") or []:
        sid = s.add(ch.get("title"), [ch.get("note"), " ".join(f"{se.get('name')}: {se.get('values')}" for se in ch.get("series") or []), ch.get("labels")])
        out.append(f'<div id="{sid}" style="margin-top:22px">{svg_chart(ch)}</div>')
    for tb in d.get("tables") or []:
        sid = s.add(tb.get("title"), [tb.get("columns"), tb.get("rows")])
        head = "".join(f"<th>{esc(c)}</th>" for c in tb.get("columns") or [])
        rows = "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in tb.get("rows") or [])
        out.append(h2(sid, tb.get("title")) + f'<div style="overflow-x:auto"><table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table></div>')
    if d.get("insights"):
        sid = s.add("洞察", d["insights"])
        out.append(h2(sid, "洞察") + ul(d["insights"]))
    return "".join(out), s.items


# ---------------------------------------------------------------- R09 教程步骤
def r09(d, ctx):
    s = Sec()
    out = [hero(d, "HOW-TO · 教程")]
    meta = []
    if d.get("audience"):
        meta.append(f'<div class="wr-card"><div class="wr-eyebrow">适合谁</div>{inline(d["audience"])}</div>')
    if d.get("time_needed"):
        meta.append(f'<div class="wr-card"><div class="wr-eyebrow">用时</div>{inline(d["time_needed"])}</div>')
    if meta:
        out.append(f'<div class="wr-grid">{"".join(meta)}</div>')
    if d.get("prerequisites"):
        sid = s.add("准备", d["prerequisites"])
        out.append(h2(sid, "开始前准备") + "<ul class=ht-check>" + "".join(f"<li>{inline(p)}</li>" for p in d["prerequisites"]) + "</ul>")
    for i, st in enumerate(d.get("steps") or [], 1):
        sid = s.add(f"第 {i} 步 {st.get('title')}", [st.get("body"), (st.get("code") or {}).get("content"), st.get("tip")])
        tip = f'<div class="wr-callout tip"><b>提示</b> {inline(st["tip"])}</div>' if st.get("tip") else ""
        out.append(f'<section class="ht-step" id="{sid}"><div class="ht-n">{i}</div><div><h3>{inline(st.get("title"))}</h3>'
                   f'{paras(st.get("body"))}{code_block(st.get("code"))}{figure(ctx, st.get("image"))}{tip}</div></section>')
    if d.get("troubleshooting"):
        sid = s.add("常见问题", [f"{t.get('problem')} {t.get('fix')}" for t in d["troubleshooting"]])
        out.append(h2(sid, "常见问题") + "".join(f'<details class="wr-card" style="margin:8px 0"><summary><b>{inline(t.get("problem"))}</b></summary>{paras(t.get("fix"))}</details>' for t in d["troubleshooting"]))
    if d.get("next_steps"):
        sid = s.add("下一步", d["next_steps"])
        out.append(h2(sid, "下一步") + paras(d["next_steps"]))
    css = (".ht-step{display:grid;grid-template-columns:44px 1fr;gap:16px;margin:28px 0}"
           ".ht-n{width:40px;height:40px;border-radius:50%;background:var(--wr-accent);color:var(--wr-ink);display:flex;align-items:center;justify-content:center;font:700 18px var(--wr-head)}"
           ".ht-check{list-style:none;padding-left:0}.ht-check li::before{content:'☐ ';color:var(--wr-accent-ink)}")
    return "".join(out), s.items, css


# ---------------------------------------------------------------- R10 方案对比与决策
def r10(d, ctx):
    s = Sec()
    out = [hero(d, "DECISION · 决策")]
    if d.get("question"):
        sid = s.add("要决定的问题", d["question"])
        out.append(f'<div class="wr-callout" id="{sid}"><b>要决定的问题</b> {inline(d["question"])}</div>')
    rec = (d.get("recommendation") or {}).get("option")
    crit = d.get("criteria") or []
    opts = d.get("options") or []
    if opts:
        sid = s.add("方案", [[o.get("name"), o.get("summary"), o.get("pros"), o.get("cons")] for o in opts])
        cards = []
        for o in opts:
            star = ' <span class="wr-pill">推荐</span>' if o.get("name") == rec else ""
            cards.append(f'<div class="wr-card{" dc-rec" if o.get("name") == rec else ""}"><h3>{esc(o.get("name"))}{star}</h3>{paras(o.get("summary"))}'
                         f'{"<p><b>成本</b> " + inline(o["cost"]) + "</p>" if o.get("cost") else ""}'
                         f'<div class="dc-pc"><div><b>优点</b>{ul(o.get("pros"))}</div><div><b>缺点</b>{ul(o.get("cons"))}</div></div></div>')
        out.append(h2(sid, "备选方案") + f'<div class="wr-grid">{"".join(cards)}</div>')
    if crit and opts:
        sid = s.add("评分矩阵", [f"{o.get('name')}: {o.get('scores')}" for o in opts])
        total_w = sum(float(c.get("weight") or 1) for c in crit) or 1
        head = "".join(f'<th>{esc(c.get("name"))} <span class="wr-pill">×{esc(c.get("weight") or 1)}</span></th>' for c in crit)
        rows = []
        for o in opts:
            sc = o.get("scores") or {}
            tot = sum(float(sc.get(c.get("name"), 0)) * float(c.get("weight") or 1) for c in crit) / total_w
            cells = "".join(f'<td>{"●" * int(sc.get(c.get("name"), 0))}<span style="opacity:.25">{"●" * (5 - int(sc.get(c.get("name"), 0)))}</span></td>' for c in crit)
            rows.append(f'<tr><td><b>{esc(o.get("name"))}</b></td>{cells}<td><b>{tot:.2f}</b></td></tr>')
        out.append(h2(sid, "评分矩阵") + f'<div style="overflow-x:auto"><table><thead><tr><th>方案</th>{head}<th>加权分</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>')
    if d.get("recommendation"):
        r = d["recommendation"]
        sid = s.add("建议", [r.get("option"), r.get("reason"), r.get("risks")])
        out.append(h2(sid, "建议") + f'<div class="wr-tldr"><b>选择</b><strong>{esc(r.get("option"))}</strong>{paras(r.get("reason"))}</div>'
                   + (f"<h3>风险</h3>{ul(r.get('risks'))}" if r.get("risks") else ""))
    if d.get("next_steps"):
        sid = s.add("下一步", d["next_steps"])
        out.append(h2(sid, "下一步") + ul(d["next_steps"]))
    css = ".dc-rec{border-color:var(--wr-accent);box-shadow:0 0 0 1px var(--wr-accent)}.dc-pc{display:grid;grid-template-columns:1fr 1fr;gap:10px;font-size:14px}"
    return "".join(out), s.items, css


# ---------------------------------------------------------------- R11 时间线 / 复盘
def r11(d, ctx):
    s = Sec()
    out = [hero(d, "TIMELINE · 时间线")]
    kinds = {"milestone": ("里程碑", "s0"), "incident": ("事故", "s1"), "release": ("发布", "s2"), "note": ("记录", "s3")}
    if d.get("events"):
        sid = s.add("时间线", [f"{e.get('date')} {e.get('title')} {e.get('body') or ''}" for e in d["events"]])
        items = []
        for e in d["events"]:
            label, c = kinds.get(e.get("kind", "note"), kinds["note"])
            items.append(f'<li><span class="tl-dot" style="background:var(--wr-{c})"></span><div class="tl-date">{esc(e.get("date"))} · {label}</div>'
                         f'<h3>{inline(e.get("title"))}</h3>{paras(e.get("body"))}</li>')
        out.append(h2(sid, "时间线") + f'<ol class="tl">{"".join(items)}</ol>')
    rt = d.get("retro") or {}
    if rt:
        sid = s.add("复盘", rt)
        cols = []
        if rt.get("went_well"):
            cols.append(f'<div class="wr-card"><h3>做得好</h3>{ul(rt["went_well"])}</div>')
        if rt.get("to_improve"):
            cols.append(f'<div class="wr-card"><h3>待改进</h3>{ul(rt["to_improve"])}</div>')
        out.append(h2(sid, "复盘") + f'<div class="wr-grid">{"".join(cols)}</div>')
        if rt.get("actions"):
            rows = "".join(f'<tr><td>{inline(a.get("task"))}</td><td>{esc(a.get("owner") or "—")}</td><td>{esc(a.get("due") or "—")}</td></tr>' for a in rt["actions"])
            out.append(f"<h3>改进行动</h3><table><thead><tr><th>事项</th><th>负责人</th><th>截止</th></tr></thead><tbody>{rows}</tbody></table>")
    css = (".tl{list-style:none;padding:0 0 0 22px;border-left:2px solid var(--wr-line);margin-left:6px}.tl li{position:relative;margin:0 0 26px}"
           ".tl-dot{position:absolute;left:-30px;top:8px;width:14px;height:14px;border-radius:50%}.tl-date{font:600 12px var(--wr-mono);color:var(--wr-muted)}.tl h3{margin:2px 0 4px}")
    return "".join(out), s.items, css


# ---------------------------------------------------------------- R12 问答 / 知识卡
def r12(d, ctx):
    s, groups = Sec(), []
    for g in d.get("groups") or []:
        sid = s.add(g.get("name"), [[i.get("q"), i.get("a")] for i in g.get("items") or []])
        qa = "".join(f'<details class="fq"><summary>{inline(i.get("q"))}</summary><div>{paras(i.get("a"))}</div></details>' for i in g.get("items") or [])
        groups.append(h2(sid, g.get("name")) + qa)
    intro = paras(d["intro"]) if d.get("intro") else ""
    css = (".fq{border-bottom:1px solid var(--wr-line);padding:12px 0}.fq summary{cursor:pointer;font-weight:600;list-style:none}"
           ".fq summary::before{content:'Q ';color:var(--wr-accent-ink);font-family:var(--wr-mono)}.fq[open] summary{color:var(--wr-accent-ink)}.fq div{padding:6px 0 0 22px}")
    return hero(d, "FAQ · 问答") + intro + toc(s.items) + "".join(groups), s.items, css


# ---------------------------------------------------------------- R13 产品发布 / 更新日志
def r13(d, ctx):
    s = Sec()
    img = figure(ctx, {"src": d["hero_image"], "alt": d.get("title")}, "ln-hero") if d.get("hero_image") else ""
    cta = d.get("cta") or {}
    btn = f'<p><a class="wr-btn" href="{esc(safe_url(cta.get("url")))}" rel="noopener noreferrer">{esc(cta.get("label"))}</a></p>' if cta.get("url") else ""
    out = [f'<header class="wr-hero ln-head"><div class="wr-eyebrow">LAUNCH · 发布</div><h1>{inline(d.get("title"))}</h1>'
           f'{"<p class=wr-sub>" + inline(d["tagline"]) + "</p>" if d.get("tagline") else ""}{btn}</header>{img}']
    if d.get("summary"):
        out.append(f'<div class="wr-tldr"><b>一句话</b>{inline(d["summary"])}</div>')
    if d.get("features"):
        sid = s.add("亮点", [[f.get("title"), f.get("body")] for f in d["features"]])
        out.append(h2(sid, "亮点") + '<div class="wr-grid">' + "".join(
            f'<div class="wr-card"><div class="ln-ic">{esc(f.get("icon") or "✦")}</div><h3>{inline(f.get("title"))}</h3>{paras(f.get("body"))}</div>' for f in d["features"]) + "</div>")
    if d.get("whats_new"):
        sid = s.add("更新日志", [[w.get("version"), [c.get("text") for c in w.get("changes") or []]] for w in d["whats_new"]])
        lab = {"new": "新增", "improved": "改进", "fixed": "修复", "removed": "移除"}
        out.append(h2(sid, "更新日志") + "".join(
            f'<div class="ln-ver"><h3>{esc(w.get("version"))} <span class="wr-meta" style="display:inline">{esc(w.get("date") or "")}</span></h3><ul>'
            + "".join(f'<li><span class="wr-pill">{lab.get(c.get("type"), "变更")}</span> {inline(c.get("text"))}</li>' for c in w.get("changes") or [])
            + "</ul></div>" for w in d["whats_new"]))
    if d.get("pricing_note"):
        sid = s.add("价格与可用性", d["pricing_note"])
        out.append(h2(sid, "价格与可用性") + paras(d["pricing_note"]))
    if btn:
        out.append(f'<div class="ln-end">{btn}</div>')
    css = (".ln-head{text-align:center;border:0}.ln-head h1{font-size:clamp(36px,6vw,64px)}.ln-hero img{width:100%}"
           ".ln-ic{font-size:26px}.ln-end{text-align:center;margin-top:36px}.ln-ver{border-left:2px solid var(--wr-line);padding-left:16px;margin:18px 0}")
    return "".join(out), s.items, css


# ---------------------------------------------------------------- R14 调研报告
def r14(d, ctx):
    s = Sec()
    out = [hero(d, "RESEARCH · 调研")]
    if d.get("question"):
        sid = s.add("研究问题", d["question"])
        out.append(f'<div class="wr-callout" id="{sid}"><b>研究问题</b> {inline(d["question"])}</div>')
    if d.get("method"):
        sid = s.add("方法", d["method"])
        out.append(h2(sid, "方法") + paras(d["method"]))
    conf = {"high": ("高置信", "s2"), "medium": ("中置信", "s4"), "low": ("低置信", "s1")}
    srcs = d.get("sources") or []
    if d.get("findings"):
        sid = s.add("发现", [[f.get("claim"), f.get("evidence")] for f in d["findings"]])
        items = []
        for i, f in enumerate(d["findings"], 1):
            label, c = conf.get(f.get("confidence", "medium"), conf["medium"])
            refs = "".join(f'<a href="#src-{int(n)}">[{int(n)}]</a>' for n in f.get("sources") or [] if 1 <= int(n) <= len(srcs))
            items.append(f'<div class="wr-card rs-f"><div class="wr-eyebrow">发现 {i:02d} · <span style="color:var(--wr-{c})">{label}</span></div>'
                         f'<h3>{inline(f.get("claim"))} <sup>{refs}</sup></h3>{paras(f.get("evidence"))}</div>')
        out.append(h2(sid, "主要发现") + "".join(items))
    if d.get("conclusion"):
        sid = s.add("结论", d["conclusion"])
        out.append(h2(sid, "结论") + paras(d["conclusion"]))
    if d.get("limitations"):
        sid = s.add("局限", d["limitations"])
        out.append(h2(sid, "局限与未验证") + paras(d["limitations"]))
    if srcs:
        sid = s.add("来源", [[x.get("title"), x.get("publisher"), x.get("date")] for x in srcs])
        out.append(h2(sid, "来源") + "<ol class=rs-src>" + "".join(
            f'<li id="src-{i}"><a href="{esc(safe_url(x.get("url")))}" rel="noopener noreferrer">{esc(x.get("title"))}</a>'
            f'{" · " + esc(x["publisher"]) if x.get("publisher") else ""}{" · " + esc(x["date"]) if x.get("date") else ""}</li>' for i, x in enumerate(srcs, 1)) + "</ol>")
    css = ".rs-f{margin:14px 0}.rs-f h3{margin:4px 0 6px}.rs-src{font-size:14px}"
    return "".join(out), s.items, css


# ---------------------------------------------------------------- R15 Agent J Noir（官方默认）
def r15(d, ctx):
    s = Sec()
    out = [hero(d, d.get("eyebrow") or "AGENT J · BRIEF")]
    if d.get("highlights"):
        sid = s.add("要点", d["highlights"])
        out.append(f'<ul class="nx-hl" id="{sid}" aria-label="要点">' + "".join(f"<li>{inline(x)}</li>" for x in d["highlights"]) + "</ul>")
    if d.get("kpis"):
        sid = s.add("关键数字", [f"{k.get('label')} {k.get('value')} {k.get('delta') or ''}" for k in d["kpis"]])
        cards = []
        for k in d["kpis"]:
            arrow = {"up": "▲", "down": "▼", "flat": "▬"}.get(k.get("trend") or "", "")
            delta = f'<div class="d nx-{esc(k.get("trend") or "flat")}">{arrow} {esc(k["delta"])}</div>' if k.get("delta") else ""
            note = f'<div class="l">{inline(k["note"])}</div>' if k.get("note") else ""
            cards.append(f'<div class="wr-card wr-kpi"><div class="l">{esc(k.get("label"))}</div><div class="v">{esc(k.get("value"))}</div>{delta}{note}</div>')
        out.append(f'<div class="wr-grid nx-kpis" id="{sid}">' + "".join(cards) + "</div>")
    body = []
    for i, sec in enumerate(d.get("sections") or [], 1):
        sid = s.add(sec.get("heading"), [sec.get("body"), [[x.get("title"), x.get("text")] for x in sec.get("items") or []],
                                         (sec.get("quote") or {}).get("text")])
        parts = [f'<h2 id="{esc(sid)}"><span class="nx-n">{i:02d}</span>{inline(sec.get("heading"))}</h2>', paras(sec.get("body"))]
        if sec.get("items"):
            lis = []
            for x in sec["items"]:
                title = inline(x.get("title"))
                if x.get("url"):
                    title = f'<a href="{esc(safe_url(x["url"]))}" rel="noopener noreferrer">{title}</a>'
                meta = f' <span class="nx-meta">{esc(x["meta"])}</span>' if x.get("meta") else ""
                lis.append(f'<li><div class="nx-t">{title}{meta}</div>{paras(x.get("text"))}</li>')
            parts.append('<ol class="nx-items">' + "".join(lis) + "</ol>")
        if sec.get("quote"):
            q = sec["quote"]
            parts.append(f'<blockquote class="wr-quote">{inline(q.get("text"))}{"<cite>— " + esc(q["cite"]) + "</cite>" if q.get("cite") else ""}</blockquote>')
        if sec.get("links"):
            parts.append('<ul class="nx-links">' + "".join(
                f'<li><a href="{esc(safe_url(l.get("url")))}" rel="noopener noreferrer">{esc(l.get("label"))}</a></li>' for l in sec["links"]) + "</ul>")
        body.append('<section class="nx-sec">' + "".join(parts) + "</section>")
    if d.get("closing"):
        sid = s.add("结语", d["closing"])
        body.append(f'<section class="nx-sec nx-close" id="{sid}">{paras(d["closing"])}</section>')
    if d.get("sources"):
        srcs = d["sources"]
        sid = s.add("来源", [[x.get("title"), x.get("publisher")] for x in srcs])
        body.append(h2(sid, "来源") + '<ol class="nx-src">' + "".join(
            (f'<li><a href="{esc(safe_url(x["url"]))}" rel="noopener noreferrer">{esc(x.get("title"))}</a>' if x.get("url") else f'<li>{esc(x.get("title"))}')
            + f'{" · " + esc(x["publisher"]) if x.get("publisher") else ""}{" · " + esc(x["date"]) if x.get("date") else ""}</li>' for x in srcs) + "</ol>")
    css = (".nx-hl{list-style:none;padding:0;margin:8px 0 28px}.nx-hl li{position:relative;padding-left:22px;margin:.55em 0}"
           ".nx-hl li::before{content:'';position:absolute;left:4px;top:.72em;width:7px;height:7px;border-radius:50%;background:var(--wr-accent)}"
           ".nx-kpis{margin:8px 0 12px}.nx-kpis .v{margin:4px 0 2px}.nx-up{color:var(--wr-s2)}.nx-down{color:var(--wr-s0)}.nx-flat{color:var(--wr-muted)}"
           ".nx-sec{margin-top:40px}.nx-n{font:600 .62em/1 var(--wr-mono);color:var(--wr-accent);margin-right:.7em;vertical-align:.18em;letter-spacing:.06em}"
           ".nx-items{padding-left:1.6em}.nx-items>li{margin:.9em 0;padding-left:.2em}.nx-items p{margin:.3em 0;color:var(--wr-muted)}"
           ".nx-t{font-weight:600}.nx-meta{font:500 12px var(--wr-mono);color:var(--wr-muted);margin-left:.5em}"
           ".nx-links{padding-left:1.2em}.nx-src{font-size:14px;color:var(--wr-muted)}.nx-close{font-size:17px}")
    return "".join(out) + "".join(body), s.items, css  # 简报不放目录：一屏一屏往下读


RENDERERS = {"R15": r15, "R01": r01, "R02": r02, "R03": r03, "R04": r04, "R05": r05, "R06": r06, "R07": r07,
             "R08": r08, "R09": r09, "R10": r10, "R11": r11, "R12": r12, "R13": r13, "R14": r14}
