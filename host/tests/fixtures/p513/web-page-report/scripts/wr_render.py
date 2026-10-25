"""网页报告渲染核心：主题（封闭调色板）、安全转义、轻量 Markdown、页面骨架、WebMCP 与结构化元数据。

纯标准库。所有用户数据先转义再拼接；URL 只放行 https / mailto / 包内相对资源。
"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent
PALETTE = json.loads((PKG / "templates" / "palette.json").read_text(encoding="utf-8"))
HEX = {c["slug"]: c["hex"] for g in PALETTE["groups"] for c in g["colors"]}
BRAND = json.loads((PKG / "templates" / "brand-themes.json").read_text(encoding="utf-8"))
ALLOWED_HEX = {h.lower() for h in HEX.values()} | {h.lower() for t in BRAND["themes"].values() for h in t["brand_colors"].values()}


def color(v: str) -> str:
    """主题里写调色板 slug，或已在 brand-themes.json 登记过的品牌色 hex。"""
    if v.startswith("#"):
        assert v.lower() in ALLOWED_HEX, f"未登记的颜色 {v}"
        return v
    return HEX[v]

# 主题只引用调色板 slug，不写裸 hex。
THEMES = {
    # Agent J Noir（官方默认，R15）：和手机 App 底部弹窗 / 系统负载 / 跳舞机器人 / Workflow 卡片同一套视觉——Noir 底、Lotion 字，
    # Peachy Feeling 点在引号块与列表符号上，Buttery 序号，Milky Waves 链接；颜色只取调色板 slug。
    "noir": {"label": "Agent J Noir · 默认深色", "dark": True, "bg": "noir", "surface": "sooty", "line": "black-mana", "text": "lotion",
             "muted": "elephant-in-the-room", "accent": "peachy-feeling", "accent_ink": "peachy-feeling", "accent2": "milky-waves",
             "soft": "sooty", "warn": "banana-republic", "font": "sans",
             "series": ["peachy-feeling", "milky-waves", "sage-sensation", "buttery", "cold-blue", "elastic-pink", "banana-republic", "somewhere-in-a-fairytale"]},
    "paper": {"label": "纸面 · 默认浅色", "bg": "lotion", "surface": "salt", "line": "last-straw", "text": "sooty",
              "muted": "argent", "accent": "milky-waves", "accent2": "peachy-feeling", "soft": "peek-a-blue",
              "warn": "banana-republic", "font": "sans"},
    "terminal": {"label": "终端 · 黑客风", "bg": "sooty", "surface": "noir", "line": "black-mana", "text": "lotion",
                 "muted": "elephant-in-the-room", "accent": "sage-sensation", "accent2": "cold-blue", "soft": "noir",
                 "warn": "pasta-luego", "font": "mono"},
    "ink": {"label": "墨色 · 深色叙事", "bg": "noir", "surface": "sooty", "line": "black-mana", "text": "lotion",
            "muted": "elephant-in-the-room", "accent": "peachy-feeling", "accent2": "banana-republic", "soft": "sooty",
            "warn": "buttery", "font": "serif"},
    "studio": {"label": "影棚 · 多媒体", "bg": "salt", "surface": "lotion", "line": "last-straw", "text": "sooty",
               "muted": "argent", "accent": "magic-carpet", "accent2": "emerald-oasis", "soft": "dust-of-the-moon",
               "warn": "banana-republic", "font": "sans"},
    "warm": {"label": "暖调 · 故事与品牌", "bg": "lotion", "surface": "white-truffle", "line": "last-straw", "text": "sooty",
             "muted": "argent", "accent": "peachy-feeling", "accent2": "emerald-oasis", "soft": "mellow-marrow",
             "warn": "banana-republic", "font": "serif"},
    # —— 品牌主题（来源见 templates/brand-themes.json）——
    "brutal": {"label": "方格纸 · 新粗野主义", "bg": "#f7f7f2", "surface": "#ffffff", "line": "#11110f", "text": "#11110f",
               "muted": "#6f6b62", "accent": "#d9ff36", "accent_ink": "#11110f", "accent2": "#df2f1f", "soft": "#d9ff36",
               "warn": "#f2e94e", "font": "brutal"},
    "desk": {"label": "研报台 · 暖色编辑风", "bg": "lotion", "surface": "salt", "line": "last-straw", "text": "sooty",
             "muted": "noir", "accent": "peachy-feeling", "accent2": "windstorm", "soft": "salt",
             "warn": "banana-republic", "font": "desk"},
    "agentj": {"label": "Agent J 标准", "bg": "lotion", "surface": "lotion", "line": "last-straw", "text": "sooty",
               "muted": "#6b6b6b", "accent": "emerald-oasis", "accent_ink": "sooty", "accent2": "emerald-oasis", "soft": "salt",
               "warn": "banana-republic", "font": "sans"},
    # —— 极客主题（来源见 templates/brand-themes.json）——
    "fixturetheme": {"label": "fixturetheme · 黑客风", "dark": True, "bg": "#1a1b26", "surface": "#1f2230", "line": "#414868", "text": "#c0caf5",
                "muted": "#8b93b8", "accent": "#9ece6a", "accent2": "#7aa2f7", "soft": "#24283b", "warn": "#e0af68", "font": "mono",
                "series": ["#9ece6a", "#7aa2f7", "#e0af68", "#f7768e", "#ad8ee6", "#449dab", "#daecc6", "#c0caf5"]},
    "herdr": {"label": "herdr · Developer", "dark": True, "bg": "#17171a", "surface": "#1e1e22", "line": "#35353d", "text": "#eae8ee",
              "muted": "#b0afb6", "accent": "#cba6f7", "accent2": "#a6e3a1", "soft": "#26262b", "warn": "#d3a027", "font": "sans",
              "head_font": "dev_display", "body_font": "dev_body",
              "series": ["#cba6f7", "#a6e3a1", "#d3a027", "#5fae74", "#6f6a86", "#b0afb6", "#eae8ee", "#35353d"]},
    "grit": {"label": "Grit · Ghostty 终端", "dark": True, "bg": "#09090b", "surface": "#18181b", "line": "#27272a", "text": "#ffffff",
             "muted": "#a1a1aa", "accent": "#fbbf24", "accent2": "#22c55e", "soft": "#18181b", "warn": "#fbbf24", "font": "sans",
             "head_font": "system", "body_font": "system",
             "series": ["#fbbf24", "#22c55e", "#ef4444", "#eab308", "#a1a1aa", "#3f3f46", "#ffffff", "#27272a"]},
}
# 品牌主题的版式签名：只改质感（描边、阴影、底纹、大小写），不改版式结构，所以任何模板都能换上。
THEME_CSS = {
    # Noir：细线用 Lotion 低透明度（不是新颜色），卡片 Sooty 一层；彩色只落在「符号」上——引号、列表点、序号、链接、单双引号强调。
    "noir": (
        ".wr-card,.wr-tldr,.wr-callout,.wr-toc,pre,.wr-tag,.wr-hero,.wr-foot,th,td{border-color:rgba(250,249,245,.14)}"
        ".wr-card{border-radius:18px;box-shadow:none}.wr-tldr{border-left:3px solid #d97757;background:#141414;border-radius:14px}"
        ".wr-tldr b{color:#d97757}.wr-eyebrow{color:#d97757;letter-spacing:.12em}"
        "h2{letter-spacing:-.01em}li::marker{color:#d97757}ol>li::marker{color:#fcbe7c;font-weight:700}"
        "a{color:#71bbde;text-decoration-color:rgba(113,187,222,.5);text-decoration-thickness:1px}a:hover{color:#faf9f5}"
        ".wr-dq{color:#d97757}.wr-sq{color:#ffe292}"
        "code{background:#141414;color:#b7e396;border-color:rgba(250,249,245,.12)}pre{background:#141414}"
        ".wr-quote,blockquote{border-left:3px solid #d97757;color:#faf9f5}.wr-quote::before{content:'\\201C';color:#d97757;margin-right:.1em}"
        ".wr-btn{background:#d97757;color:#141414}.wr-tag{border-color:rgba(217,119,87,.55);color:#faf9f5}"
        "::selection{background:#d97757;color:#141414}"),
    "brutal": (
        "body{background-image:linear-gradient(rgba(17,17,15,.043) 1px,transparent 1px),linear-gradient(90deg,rgba(17,17,15,.043) 1px,transparent 1px);background-size:28px 28px}"
        "h1,h2{text-transform:uppercase;letter-spacing:-.02em}h1{font-size:clamp(40px,6vw,80px)}"
        ".wr-card,.wr-tldr,.wr-callout,.wr-toc,pre{border:3px solid var(--wr-line);border-radius:8px;box-shadow:8px 8px 0 var(--wr-line)}"
        ".wr-tldr{border-left-width:3px}.wr-hero{border-bottom:3px solid var(--wr-line)}"
        ".wr-tag,.wr-pill{border:2px solid var(--wr-line);font-family:var(--wr-mono);text-transform:uppercase;font-weight:700}"
        ".wr-eyebrow{color:var(--wr-text);font-weight:700}.wr-btn{border:3px solid var(--wr-line);box-shadow:5px 5px 0 var(--wr-line);text-transform:uppercase}"
        "a{background:linear-gradient(transparent 62%,var(--wr-accent) 62%);text-decoration:none}a:hover{color:var(--wr-text);background:var(--wr-warn)}"
        ".wr-card:hover{transform:translate(-2px,-2px);box-shadow:10px 10px 0 var(--wr-line)}"
        ".wr-foot{background:var(--wr-text);color:var(--wr-bg);border:0;padding:16px 20px;border-radius:8px}"),
    "desk": (
        "h1,h2{text-transform:uppercase;letter-spacing:0}.wr-eyebrow{color:var(--wr-accent);font-family:var(--wr-font);font-weight:700}"
        ".wr-card{border-radius:8px;box-shadow:none}.wr-tldr{border-left:6px solid var(--wr-accent);background:var(--wr-surface)}"
        ".wr-callout{border-left-width:6px}.wr-quote{border-left-width:6px;font-family:var(--wr-font);text-transform:uppercase}"
        ".wr-btn{background:var(--wr-text);color:var(--wr-bg);border-radius:6px}"
        "main.wr-wrap{border-top:6px solid var(--wr-text)}"
        ".wr-foot{background:var(--wr-text);color:var(--wr-bg);border:0;padding:16px 20px}"),
    "agentj": (
        "h1{letter-spacing:-.02em}h2{letter-spacing:-.01em}.wr-eyebrow{font:500 15px/1.4 var(--wr-font);letter-spacing:.04em;text-transform:none}"
        ".wr-card{border-radius:16px;box-shadow:0 1px 2px rgba(20,20,20,.05),0 6px 24px rgba(20,20,20,.08)}"
        ".wr-tldr{border-left:0;border:1px solid var(--wr-line);border-radius:16px;background:var(--wr-soft)}"
        ".wr-hero{border-bottom-style:dashed}.wr-foot{border-top-style:dashed}h2{padding-top:18px;border-top:1px dashed var(--wr-line)}"
        "a{color:var(--wr-text);text-decoration-color:var(--wr-accent);text-decoration-thickness:2px}"
        ".wr-btn{background:var(--wr-text);color:var(--wr-bg);border-radius:10px}.wr-btn:hover{background:var(--wr-surface);color:var(--wr-text)}"
        ".wr-tag{border-radius:999px}"),
    # fixturetheme：全等宽、0 圆角、无阴影、1px 白色细环、像素方块底纹、闪烁块状光标（只用 CSS，尊重减少动效设置）。
    "fixturetheme": (
        "*{border-radius:0!important}.wr-card,.wr-tldr,.wr-callout,.wr-toc,pre,.wr-tag,.wr-pill{box-shadow:0 0 0 1px rgba(255,255,255,.07)}"
        "body::before{content:'';position:fixed;inset:0 0 auto 0;height:220px;pointer-events:none;z-index:-1;"
        "background-image:conic-gradient(#39482e 25%,transparent 0);background-size:10px 10px;opacity:.55;"
        "-webkit-mask-image:linear-gradient(#000,transparent);mask-image:linear-gradient(#000,transparent)}"
        ".wr-eyebrow::before{content:'~/ ';color:var(--wr-accent)}h1::after{content:'█';color:var(--wr-accent);margin-left:.15em;animation:wr-caret 1.06s step-end infinite}"
        "@keyframes wr-caret{50%{opacity:0}}h2{color:var(--wr-text)}h2::before{content:'# ';color:var(--wr-accent);opacity:1}"
        ".wr-hero,.wr-tldr,.wr-callout,.wr-toc{animation:wr-rise .5s ease-out both}.wr-tldr{animation-delay:.09s}.wr-toc{animation-delay:.18s}"
        "@keyframes wr-rise{from{opacity:0;transform:translateY(14px)}}"
        ".wr-tldr{background:var(--wr-surface);border-left:2px solid var(--wr-accent)}.wr-btn{color:#0c0e10}"
        "a{text-decoration:none;border-bottom:1px solid var(--wr-accent)}::selection{background:#24283b}"
        "@media (prefers-reduced-motion:reduce){h1::after,.wr-hero,.wr-tldr,.wr-callout,.wr-toc{animation:none}}"),
    # herdr：72px 方格线、0 圆角 1px 描边、超粗紧排标题、大写字距标签 + 强调短线、代码块 $ 提示符。
    "herdr": (
        "body{background-image:linear-gradient(#202024 1px,transparent 1px),linear-gradient(90deg,#202024 1px,transparent 1px);background-size:72px 72px}"
        "*{border-radius:0!important}h1{font-weight:900;letter-spacing:-.03em;line-height:1.04;font-size:clamp(40px,7vw,84px)}"
        ":lang(en) h1{letter-spacing:-.055em;line-height:.92}"
        "h2,h3{font-weight:800;letter-spacing:-.03em}"
        ".wr-eyebrow{font-size:10.5px;letter-spacing:.2em;text-transform:uppercase;color:var(--wr-muted)}"
        ".wr-eyebrow::before{content:'';display:inline-block;width:42px;height:1px;background:var(--wr-accent);vertical-align:middle;margin-right:12px}"
        ".wr-card,.wr-tldr,.wr-callout,.wr-toc,pre{border:1px solid var(--wr-line);box-shadow:none}"
        ".wr-tldr{background:var(--wr-surface);border-left:1px solid var(--wr-line);border-top:2px solid var(--wr-accent)}"
        ".wr-kpi .v{font-weight:900;letter-spacing:-.04em;font-size:40px}.wr-tag,.wr-pill{text-transform:uppercase;letter-spacing:.07em;font-size:11px}"
        "pre{position:relative;padding-left:34px}pre::before{content:'$';position:absolute;left:14px;color:var(--wr-accent)}"
        ".wr-btn{background:var(--wr-accent);color:var(--wr-bg);text-transform:uppercase;letter-spacing:.07em}"
        ".ht-n{background:transparent;border:1px solid var(--wr-accent);color:var(--wr-accent)}"
        "a{color:var(--wr-accent)}"),
    # Grit：zinc 近黑、1px 描边、6px 圆角、无阴影；卡片与代码块带终端窗口标题栏（三色圆点）、>_ 前缀、琥珀单一强调。
    "grit": (
        ".wr-card,.wr-tldr,.wr-callout,.wr-toc,pre{border:1px solid var(--wr-line);border-radius:6px;box-shadow:none}"
        ".wr-card,pre{position:relative;padding-top:38px}"
        ".wr-card::before,pre::before{content:'';position:absolute;left:0;right:0;top:0;height:26px;border-bottom:1px solid var(--wr-line);"
        "background:radial-gradient(circle at 14px 13px,#ef4444 4px,transparent 4.5px),radial-gradient(circle at 27px 13px,#eab308 4px,transparent 4.5px),"
        "radial-gradient(circle at 40px 13px,#22c55e 4px,transparent 4.5px)}"
        ".wr-card:hover{border-color:#3f3f46}.wr-eyebrow::before{content:'>_ ';color:var(--wr-text);font-family:var(--wr-mono)}"
        "h1{letter-spacing:-.025em}.wr-hero{border-bottom:1px solid var(--wr-line)}"
        ".wr-tldr{border-left:2px solid var(--wr-accent)}a{color:var(--wr-text);text-decoration-color:var(--wr-accent)}"
        ".wr-btn{background:var(--wr-surface);color:var(--wr-text);border:1px solid var(--wr-accent)}"
        ".tl-dot{border-radius:2px!important}"),
}
# 图表系列色：只在调色板内循环。
SERIES = ["milky-waves", "peachy-feeling", "emerald-oasis", "magic-carpet", "buttery", "elastic-pink", "cold-blue", "lama"]
SERIES_DARK = ["sage-sensation", "cold-blue", "pasta-luego", "strawberry-field", "somewhere-in-a-fairytale", "buttery", "milky-waves", "elastic-pink"]

FONTS = {
    "sans": '"Ubuntu", "Lexend", "PingFang SC", "Noto Sans CJK SC", "Microsoft YaHei", ui-sans-serif, system-ui, sans-serif',
    "title": '"Lexend", "Ubuntu", "PingFang SC", "Noto Sans CJK SC", "Microsoft YaHei", ui-sans-serif, system-ui, sans-serif',
    "serif": '"Noto Serif CJK SC", "Source Han Serif SC", "Songti SC", Georgia, ui-serif, serif',
    "mono": '"JetBrains Mono", "Ubuntu Mono", "Cascadia Code", "Noto Sans Mono CJK SC", ui-monospace, monospace',
    "dev_display": '"Archivo", "Inter", "Helvetica Neue", "PingFang SC", "Noto Sans CJK SC", "Microsoft YaHei", ui-sans-serif, system-ui, sans-serif',
    "dev_body": '"Inter", "Geist", "PingFang SC", "Noto Sans CJK SC", "Microsoft YaHei", ui-sans-serif, system-ui, sans-serif',
    "system": 'ui-sans-serif, system-ui, -apple-system, "Segoe UI", "PingFang SC", "Noto Sans CJK SC", "Microsoft YaHei", sans-serif',
    "system_mono": 'ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", "Noto Sans Mono CJK SC", monospace',
    "display": 'Impact, "Arial Black", "Haettenschweiler", "PingFang SC", "Noto Sans CJK SC", "Microsoft YaHei", sans-serif',
}


def esc(v) -> str:
    return html.escape("" if v is None else str(v), quote=True)


SAFE_URL = re.compile(r"^(https://[^\s\"'<>]+|mailto:[^\s\"'<>]+|assets/[\w.\-/]+|#[\w\-]+)$")


def safe_url(u) -> str:
    u = (u or "").strip()
    return u if SAFE_URL.match(u) and ".." not in u else "#"


QUOTE_SPANS = ((re.compile(r"“([^”\n<>]{1,300})”"), "wr-dq", "“", "”"),
               (re.compile(r"&quot;([^&\n<>]{1,300}?)&quot;"), "wr-dq", "&quot;", "&quot;"),
               (re.compile(r"‘([^’\n<>]{1,300})’"), "wr-sq", "‘", "’"),
               (re.compile(r"「([^」\n<>]{1,300})」"), "wr-sq", "「", "」"))


def quote_spans(s: str) -> str:
    """已转义的文字里，双引号 “…” / "…" 标 wr-dq，单引号 ‘…’ / 「…」 标 wr-sq（引号本身保留）。只有 Noir 等主题给它们上色。"""
    for rx, cls, a, b in QUOTE_SPANS:
        s = rx.sub(lambda m: f'<span class="{cls}">{a}{m.group(1)}{b}</span>', s)
    return s


def inline(text) -> str:
    """转义后再做极小的行内 Markdown：**粗体** *斜体* `代码` [文字](https://…)，以及单双引号强调。"""
    s = quote_spans(esc(text))
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<![*\w])\*([^*\n]+)\*(?![*\w])", r"<em>\1</em>", s)

    def link(m):
        url = html.unescape(m.group(2))
        return f'<a href="{esc(safe_url(url))}" rel="noopener noreferrer">{m.group(1)}</a>'
    return re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", link, s)


def paras(text, cls="") -> str:
    """空行分段，单换行转 <br>；以「- 」开头的段落渲染成列表。"""
    out = []
    for block in re.split(r"\n\s*\n", (text or "").strip()):
        if not block.strip():
            continue
        lines = block.strip().split("\n")
        if all(l.lstrip().startswith(("- ", "* ")) for l in lines):
            out.append("<ul>" + "".join(f"<li>{inline(l.lstrip()[2:])}</li>" for l in lines) + "</ul>")
        else:
            c = f' class="{cls}"' if cls else ""
            out.append(f"<p{c}>" + "<br>".join(inline(l) for l in lines) + "</p>")
    return "\n".join(out)


def slug(s, i=0) -> str:
    base = re.sub(r"[^\w\-]+", "-", str(s or "").lower(), flags=re.U).strip("-")[:40]
    return f"s{i}-{base}" if base else f"s{i}"


def theme_css(name: str) -> str:
    t = THEMES[name]
    v = {k: color(t[k]) for k in ("bg", "surface", "line", "text", "muted", "accent", "accent2", "soft", "warn")}
    v["accent-ink"] = color(t.get("accent_ink", t["accent"]))
    dark = t.get("dark", name in ("terminal", "ink"))
    series = t.get("series") or (SERIES_DARK if dark else SERIES)  # 主题可自带图表系列色（slug 或已登记品牌色）
    body_font = FONTS[t["body_font"]] if "body_font" in t else {"mono": FONTS["mono"], "brutal": FONTS["serif"]}.get(t["font"], FONTS["sans"])
    head_font = FONTS[t["head_font"]] if "head_font" in t else {"mono": FONTS["mono"], "serif": FONTS["serif"], "brutal": FONTS["display"]}.get(t["font"], FONTS["title"])
    lines = [f"--wr-{k}:{h};" for k, h in v.items()]
    lines += [f"--wr-s{i}:{color(c)};" for i, c in enumerate(series)]
    lines += [f"--wr-ink:{HEX['sooty']};", f"--wr-paper:{HEX['lotion']};",
              f"--wr-font:{body_font};", f"--wr-head:{head_font};", f"--wr-mono:{FONTS['mono']};"]
    return ":root{" + "".join(lines) + "}"


BASE_CSS = """
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--wr-bg);color:var(--wr-text);font:16px/1.75 var(--wr-font);}
a{color:var(--wr-accent-ink);text-underline-offset:3px}a:hover{color:var(--wr-accent2)}
code{font-family:var(--wr-mono);font-size:.9em;background:var(--wr-surface);border:1px solid var(--wr-line);padding:.05em .35em;border-radius:4px}
pre{font-family:var(--wr-mono);font-size:14px;line-height:1.6;background:var(--wr-surface);border:1px solid var(--wr-line);border-radius:10px;padding:16px 18px;overflow:auto}
pre code{background:none;border:0;padding:0}
img,video,iframe{max-width:100%;border-radius:10px}
h1,h2,h3{font-family:var(--wr-head);line-height:1.25;margin:0 0 .5em}
h1{font-size:clamp(30px,5vw,48px);letter-spacing:-.01em}h2{font-size:clamp(22px,3vw,28px);margin-top:1.6em}h3{font-size:19px}
.wr-wrap{max-width:860px;margin:0 auto;padding:48px 22px 80px}
.wr-wide{max-width:1120px}
.wr-hero{padding:8px 0 28px;border-bottom:1px solid var(--wr-line);margin-bottom:28px}
.wr-eyebrow{font:600 12px/1.4 var(--wr-mono);letter-spacing:.14em;text-transform:uppercase;color:var(--wr-muted);margin-bottom:14px}
.wr-sub{font-size:19px;color:var(--wr-muted);margin:.2em 0 0}
.wr-meta{display:flex;flex-wrap:wrap;gap:8px 18px;color:var(--wr-muted);font-size:14px;margin-top:16px}
.wr-tag{display:inline-block;border:1px solid var(--wr-line);border-radius:999px;padding:1px 10px;font-size:12px;margin:0 6px 6px 0;color:var(--wr-text)}
.wr-tldr{background:var(--wr-soft);border-left:3px solid var(--wr-accent);border-radius:8px;padding:14px 18px;margin:22px 0}
.wr-tldr b{display:block;font:600 12px var(--wr-mono);letter-spacing:.12em;color:var(--wr-muted);margin-bottom:4px}
.wr-card{background:var(--wr-surface);border:1px solid var(--wr-line);border-radius:14px;padding:18px 20px}
.wr-grid{display:grid;gap:16px;grid-template-columns:repeat(auto-fit,minmax(220px,1fr))}
.wr-kpi .v{font:700 32px/1.1 var(--wr-head)}.wr-kpi .l{color:var(--wr-muted);font-size:13px}
.wr-kpi .d{font:600 13px var(--wr-mono)}.up{color:var(--wr-s2)}.down{color:var(--wr-s1)}
table{width:100%;border-collapse:collapse;font-size:15px;margin:12px 0}
th,td{text-align:left;padding:9px 10px;border-bottom:1px solid var(--wr-line);vertical-align:top}
th{font-weight:600;color:var(--wr-muted);font-size:13px}
.wr-callout{border:1px solid var(--wr-line);border-left:3px solid var(--wr-accent2);border-radius:8px;padding:12px 16px;margin:16px 0;background:var(--wr-surface)}
.wr-callout.warn{border-left-color:var(--wr-warn)}.wr-callout.tip{border-left-color:var(--wr-s2)}
.wr-quote{font:italic 22px/1.6 var(--wr-head);border-left:3px solid var(--wr-accent);padding:4px 0 4px 20px;margin:28px 0}
.wr-quote cite{display:block;font:normal 14px var(--wr-font);color:var(--wr-muted);margin-top:6px}
.wr-pill{display:inline-block;font:600 12px var(--wr-mono);border-radius:6px;padding:1px 8px;background:var(--wr-soft);color:var(--wr-text)}
.wr-toc{font-size:14px;border:1px solid var(--wr-line);border-radius:12px;padding:12px 18px;margin:0 0 28px}
.wr-toc a{color:var(--wr-text);text-decoration:none}.wr-toc a:hover{color:var(--wr-accent-ink)}
.wr-foot{margin-top:56px;padding-top:18px;border-top:1px solid var(--wr-line);color:var(--wr-muted);font-size:13px;display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap}
.wr-btn{display:inline-block;border-radius:10px;padding:10px 18px;background:var(--wr-accent);color:var(--wr-ink);text-decoration:none;font-weight:600;border:0;cursor:pointer;font:inherit}
.wr-btn.alt{background:var(--wr-surface);color:var(--wr-text);border:1px solid var(--wr-line)}
figure{margin:22px 0}figcaption{color:var(--wr-muted);font-size:14px;margin-top:6px}
ul,ol{padding-left:1.3em}li{margin:.25em 0}
@media print{body{background:var(--wr-paper);color:var(--wr-ink)}.wr-noprint{display:none}}
@media (max-width:640px){.wr-wrap{padding:28px 16px 60px}body{font-size:15px}}
"""


WEBMCP_FILE = "_wr-webmcp.js"  # 打包进每份报告的 assets/，同源加载
WEBMCP_SRC = Path(__file__).resolve().parent / "webmcp.js"


def flatten_text(v) -> str:
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        return "\n".join(flatten_text(x) for k, x in v.items() if not str(k).endswith(("src", "url", "poster", "image")))
    if isinstance(v, list):
        return "\n".join(flatten_text(x) for x in v)
    return "" if v is None or isinstance(v, bool) else str(v)


def csp(frames: bool) -> str:
    """页面 CSP：脚本只允许同源文件（WebMCP 脚本外置在 assets/），不在页面里写任何哈希。"""
    frame = " frame-src https://www.youtube-nocookie.com https://player.bilibili.com https://player.vimeo.com;" if frames else " frame-src 'none';"
    return ("default-src 'none'; img-src 'self' https: data:; media-src 'self' https:; style-src 'unsafe-inline';"
            f" script-src 'self'; connect-src 'none'; base-uri 'none'; form-action 'self';{frame}")


def page(*, data: dict, template: dict, body: str, sections: list[dict], theme: str | None = None,
         expires_at: str | None = None, public: bool = True, frames: bool = False, wide: bool = False,
         extra_css: str = "") -> str:
    """组装完整 HTML。sections = [{id, heading, text}]，供 WebMCP 与目录使用。"""
    theme = theme or data.get("theme") or template["default_theme"]
    if theme not in THEMES:
        theme = template["default_theme"]
    lang = data.get("lang") or "zh-CN"
    title = data.get("title", "")
    desc = data.get("summary") or data.get("subtitle") or title
    meta = {"title": title, "template": template["id"], "template_name": template["name_zh"], "author": data.get("author"),
            "date": data.get("date"), "lang": lang, "expires_at": expires_at, "generator": "web-page-report"}
    payload = {"meta": meta, "outline": [{"id": s["id"], "heading": s["heading"]} for s in sections],
               "sections": sections, "data": data}
    data_json = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    ld = {"@context": "https://schema.org", "@type": "Report", "name": title, "description": desc, "inLanguage": lang,
          "datePublished": data.get("date"), "author": {"@type": "Person", "name": data["author"]} if data.get("author") else None,
          "keywords": ", ".join(data.get("tags") or []) or None, "expires": expires_at,
          "isAccessibleForFree": True}
    ld = {k: v for k, v in ld.items() if v is not None}
    ld_json = json.dumps(ld, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    robots = "noindex,nofollow,noarchive" if not public or not data.get("indexable") else "index,follow"
    og_img = data.get("cover_image") if str(data.get("cover_image", "")).startswith("https://") else None
    head = [
        '<meta charset="utf-8">', '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f'<meta http-equiv="Content-Security-Policy" content="{esc(csp(frames))}">',
        f"<title>{esc(title)}</title>", f'<meta name="description" content="{esc(desc[:300])}">',
        f'<meta name="robots" content="{robots}">', '<meta name="referrer" content="strict-origin-when-cross-origin">',
        f'<meta name="generator" content="web-page-report {template["id"]}">',
        f'<meta property="og:type" content="article"><meta property="og:title" content="{esc(title)}">',
        f'<meta property="og:description" content="{esc(desc[:300])}">',
        f'<meta name="report:template" content="{esc(template["id"])}">',
        f'<meta name="report:expires" content="{esc(expires_at or "never")}">',
        '<meta name="webmcp:tools" content="report_get_metadata report_get_outline report_get_section report_get_full report_search">',
        '<link rel="alternate" type="application/json" href="report.json" title="Structured report data">',
    ]
    if og_img:
        head.append(f'<meta property="og:image" content="{esc(og_img)}">')
    head += [f'<script type="application/ld+json">{ld_json}</script>',
             f"<style>{theme_css(theme)}{BASE_CSS}{extra_css}{THEME_CSS.get(theme, '')}</style>"]  # 品牌签名最后加载，覆盖基础样式
    foot_note = inline(data.get("footer_note")) if data.get("footer_note") else ""
    exp = f"有效期至 {esc(expires_at[:10])}" if expires_at else ""
    footer = (f'<footer class="wr-foot"><span>{foot_note or esc(data.get("author") or "")}</span>'
              f'<span>{exp}{" · " if exp else ""}网页报告 · {esc(template["id"])}</span></footer>')
    wrap = "wr-wrap wr-wide" if wide else "wr-wrap"
    return ("<!doctype html>\n" f'<html lang="{esc(lang)}" data-theme="{theme}" data-template="{esc(template["id"])}">'
            "<head>" + "".join(head) + "</head><body>"
            f'<main class="{wrap}">{body}{footer}</main>'
            f'<script type="application/json" id="wr-data">{data_json}</script>'
            f'<script src="assets/{WEBMCP_FILE}" defer></script></body></html>\n')


def hero(data: dict, eyebrow: str) -> str:
    meta = []
    if data.get("author"):
        meta.append(f"<span>{esc(data['author'])}</span>")
    if data.get("date"):
        meta.append(f"<span>{esc(data['date'])}</span>")
    tags = "".join(f'<span class="wr-tag">{esc(t)}</span>' for t in data.get("tags") or [])
    sub = f'<p class="wr-sub">{inline(data["subtitle"])}</p>' if data.get("subtitle") else ""
    tldr = f'<div class="wr-tldr"><b>TL;DR</b>{inline(data["summary"])}</div>' if data.get("summary") else ""
    return (f'<header class="wr-hero"><div class="wr-eyebrow">{esc(eyebrow)}</div><h1>{inline(data.get("title"))}</h1>{sub}'
            f'<div class="wr-meta">{"".join(meta)}</div>{"<div style=margin-top:12px>" + tags + "</div>" if tags else ""}</header>{tldr}')


def toc(sections: list[dict]) -> str:
    if len(sections) < 3:
        return ""
    items = "".join(f'<li><a href="#{esc(s["id"])}">{esc(s["heading"])}</a></li>' for s in sections)
    return f'<nav class="wr-toc wr-noprint" aria-label="目录"><ol>{items}</ol></nav>'
