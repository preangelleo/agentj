"""P189: one conversation turn as an e-mail — 「你问」 above, 「Agent J 答」 below — in HTML and plain text.

Built for mail clients, not browsers: table layout, every style inline (Gmail, Outlook, Apple Mail, QQ 邮箱 drop or ignore
<style> in different ways), a 600 px column that shrinks on phones, font stacks with system fallbacks (no web fonts), and
only colours from Leo's palette (brand/palette.css). A small <style> block adds the dark look for clients that honour
`prefers-color-scheme` (Apple Mail, iOS Mail, Outlook for Mac) and Outlook.com's `[data-ogsc]`; clients that invert on
their own (Gmail apps, Outlook for Windows) get a light design whose chips and lines survive inversion.

The reply's Markdown (the subset the phone's reader draws: headings, paragraphs, lists — nested by indent —, block quotes,
fenced code, pipe tables, rules; inline code, bold, italic, strike, links) becomes HTML here. Every character of the
owner's or the Agent's text is escaped; only http(s) / mailto links become anchors. Pure: no I/O, same input → same output.
"""
from __future__ import annotations

import html
import re
import time
from urllib.parse import urlsplit

SUBJECT_CHARS = 40
BRAND = "Agent J"
SITE = "https://agentj.app"

# Leo's palette (agentjarvis/brand/palette.css) — no other colour may appear in a mail.
LOTION, SALT, SOOTY, NOIR = "#faf9f5", "#f0eee6", "#141414", "#2c2b26"
STRAW, MANA, ARGENT, ELEPHANT = "#e3dacc", "#858585", "#888888", "#acacac"
MARROW, MOON, WINDSTORM = "#ebc9b7", "#cacadb", "#6a9bcc"
PALETTE = (LOTION, SALT, SOOTY, NOIR, STRAW, MANA, ARGENT, ELEPHANT, MARROW, MOON, WINDSTORM)

BODY_FONT = "Ubuntu,'Trebuchet MS',Tahoma,'PingFang SC','Hiragino Sans GB','Microsoft YaHei','Noto Sans CJK SC',Arial,sans-serif"
TITLE_FONT = "Lexend,Ubuntu,'Trebuchet MS',Tahoma,'PingFang SC','Microsoft YaHei',Arial,sans-serif"
MONO_FONT = "SFMono-Regular,Menlo,Consolas,'Liberation Mono','Courier New',monospace"

T = {
    "zh": {"asked": "你问", "answered": "{agent} 答", "from": "来自 {name}", "quote": "引用 {who}：",
           "sent_files": "随消息发送的文件：", "files": "附件", "too_big": "文件太大未附上", "gone": "文件已不在，未附上",
           "held": "未附上",
           "kind": {"task": "定时任务", "agent": "{agent} 自己发起", "sys": "系统", "cmd": "命令", "telegram": "Telegram",
                    "email": "邮件"},
           "foot": "这封邮件是从 {agent} 的手机遥控器转发的。", "powered": "Powered by Agent J", "lang": "zh-CN"},
    "en": {"asked": "You asked", "answered": "{agent} answered", "from": "from {name}", "quote": "Quoting {who}: ",
           "sent_files": "Files sent with the message: ", "files": "Attachments", "too_big": "Too big, not attached",
           "gone": "No longer there, not attached", "held": "Not attached",
           "kind": {"task": "Scheduled task", "agent": "Started by {agent}", "sys": "System", "cmd": "Command",
                    "telegram": "Telegram", "email": "E-mail"},
           "foot": "Forwarded from {agent}'s phone remote.", "powered": "Powered by Agent J", "lang": "en"},
}

_DARK_CSS = (
    ":root{color-scheme:light dark;supported-color-schemes:light dark}"
    "@media (prefers-color-scheme:dark){"
    f".aj-bg{{background:{SOOTY}!important}}.aj-card{{background:{NOIR}!important;border-color:{MANA}!important}}"
    f".aj-tx,.aj-tx p,.aj-tx li,.aj-tx td,.aj-tx th,.aj-tx h1,.aj-tx h2,.aj-tx h3,.aj-tx h4{{color:{LOTION}!important}}"
    f".aj-sub{{color:{ELEPHANT}!important}}.aj-code{{background:{SOOTY}!important;border-color:{MANA}!important;color:{LOTION}!important}}"
    f".aj-th{{background:{SOOTY}!important}}.aj-tb{{border-color:{MANA}!important}}.aj-a{{color:{LOTION}!important}}"
    f".aj-q{{border-color:{MANA}!important;color:{LOTION}!important}}.aj-qb{{background:{SOOTY}!important;color:{ELEPHANT}!important}}"
    f".aj-seam{{border-color:{MANA}!important}}.aj-foot{{background:{NOIR}!important}}}}"
    f"[data-ogsc] .aj-tx,[data-ogsc] .aj-tx p,[data-ogsc] .aj-tx li,[data-ogsc] .aj-tx td{{color:{LOTION}!important}}"
    f"[data-ogsb] .aj-card{{background:{NOIR}!important}}[data-ogsb] .aj-bg{{background:{SOOTY}!important}}"
    "@media (max-width:620px){.aj-col{width:100%!important}.aj-pad{padding-left:12px!important;padding-right:12px!important}"
    ".aj-in{padding:14px 14px 6px!important}}"
)


def esc(s) -> str:
    return html.escape(str(s or ""), quote=True)


# ---------------------------------------------------------------- Markdown → mail HTML
_INLINE = re.compile(r"(`+)(.+?)\1|\[([^\]\n]+)\]\(((?:[^\s()]|\([^\s()]*\))+)\)|(https?://[^\s<>()\[\]]+[^\s<>()\[\].,;:!?'\"，。；：！？」）])"
                     r"|(\*\*|(?<!\w)__|~~|\*|(?<!\w)_)(?=\S)(.+?)(?<=\S)\6", re.S)
_TAG = {"**": "strong", "__": "strong", "*": "em", "_": "em", "~~": "s"}
_CODE_IN = (f"background:{SALT};border:1px solid {STRAW};border-radius:4px;padding:1px 5px;font-family:{MONO_FONT};"
            "font-size:13px;word-break:break-word")
_LINK = f"color:{SOOTY};text-decoration:underline;text-decoration-color:{WINDSTORM};text-underline-offset:2px"


def _link_ok(u: str) -> bool:
    try:
        return len(u) <= 2048 and urlsplit(u).scheme.lower() in ("http", "https", "mailto")
    except ValueError:
        return False


def inline(text: str, depth: int = 0) -> str:
    if depth >= 4:
        return esc(text)
    out, end = [], 0
    for m in _INLINE.finditer(text):
        out.append(esc(text[end:m.start()]))
        if m[1]:
            out.append(f'<code class="aj-code" style="{_CODE_IN}">{esc(m[2])}</code>')
        elif m[3]:
            label = inline(m[3], depth + 1)
            out.append(f'<a class="aj-a" href="{esc(m[4])}" style="{_LINK}">{label}</a>' if _link_ok(m[4]) else label)
        elif m[5]:
            out.append(f'<a class="aj-a" href="{esc(m[5])}" style="{_LINK}">{esc(m[5])}</a>')
        elif m[6][0] == "_" and m.end() < len(text) and re.match(r"\w", text[m.end()]):
            out.append(esc(m[0]))
        else:
            tag = _TAG[m[6]]
            out.append(f"<{tag}>{inline(m[7], depth + 1)}</{tag}>")
        end = m.end()
    out.append(esc(text[end:]))
    return "".join(out)


_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_HEAD = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_HR = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
_ITEM = re.compile(r"^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$")
_SEP = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")
_P = "margin:0 0 12px;font-size:15px;line-height:1.65"
_H = {1: 21, 2: 19, 3: 17, 4: 16, 5: 15, 6: 15}


def _cells(line: str) -> list[str]:
    s = line.strip()
    s = s[1:] if s.startswith("|") else s
    s = s[:-1] if s.endswith("|") and not s.endswith("\\|") else s
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", s)]


def _nw(text: str) -> str:
    return ";white-space:nowrap" if len(text) <= 4 else ""


def _table(head: list[str], rows: list[list[str]], aligns: list[str]) -> str:
    n = len(head)
    cell = f"border:1px solid {STRAW};padding:6px 8px;font-size:14px;line-height:1.5;vertical-align:top"
    th = "".join(f'<th class="aj-tb aj-th" align="{aligns[i]}" style="{cell};background:{SALT};font-weight:600;text-align:{aligns[i]}{_nw(c)}">'
                 f"{inline(c)}</th>" for i, c in enumerate(head))
    body = "".join("<tr>" + "".join(f'<td class="aj-tb" align="{aligns[i]}" style="{cell};text-align:{aligns[i]}{_nw(r[i] if i < len(r) else "")}">{inline(r[i] if i < len(r) else "")}</td>'
                                    for i in range(n)) + "</tr>" for r in rows)
    return (f'<div style="margin:0 0 12px;overflow-x:auto"><table cellpadding="0" cellspacing="0" border="0" '
            f'style="border-collapse:collapse;min-width:60%;max-width:100%"><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>')


def markdown(text: str, depth: int = 0) -> str:
    lines = str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []
    para: list[str] = []

    def flush():
        if para:
            out.append(f'<p style="{_P}">' + "<br>".join(inline(x.strip()) for x in para) + "</p>")
            para.clear()
    i = 0
    while i < len(lines):
        ln = lines[i]
        f = _FENCE.match(ln)
        if f:
            flush()
            mark, code = f[1], []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith(mark[0] * len(mark)):
                code.append(lines[i])
                i += 1
            i += 1
            out.append(f'<div class="aj-code" style="background:{SALT};border:1px solid {STRAW};border-radius:8px;padding:12px 14px;'
                       f'margin:0 0 12px;overflow-x:auto"><pre style="margin:0;font-family:{MONO_FONT};font-size:13px;'
                       f'line-height:1.55;white-space:pre-wrap;word-break:break-word;color:inherit">{esc(chr(10).join(code))}</pre></div>')
            continue
        if not ln.strip():
            flush()
            i += 1
            continue
        h = _HEAD.match(ln)
        if h:
            flush()
            n = len(h[1])
            out.append(f'<h{n} style="margin:18px 0 8px;font-family:{TITLE_FONT};font-size:{_H[n]}px;line-height:1.35;'
                       f'font-weight:700">{inline(h[2])}</h{n}>')
            i += 1
            continue
        if _HR.match(ln) and not para:
            out.append(f'<hr class="aj-seam" style="border:0;border-top:1px solid {STRAW};margin:16px 0">')
            i += 1
            continue
        if "|" in ln and i + 1 < len(lines) and "|" in lines[i + 1] and _SEP.match(lines[i + 1]):
            flush()
            head = _cells(ln)
            aligns = []
            for c in _cells(lines[i + 1])[:len(head)]:
                aligns.append("center" if c.startswith(":") and c.endswith(":") else "right" if c.endswith(":") else "left")
            aligns += ["left"] * (len(head) - len(aligns))
            rows = []
            i += 2
            while i < len(lines) and lines[i].strip() and "|" in lines[i]:
                rows.append(_cells(lines[i]))
                i += 1
            out.append(_table(head, rows, aligns))
            continue
        if ln.lstrip().startswith(">") and depth < 4:
            flush()
            q = []
            while i < len(lines) and lines[i].lstrip().startswith(">"):
                q.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            out.append(f'<blockquote class="aj-q" style="margin:0 0 12px;padding:2px 0 2px 14px;border-left:3px solid {STRAW};'
                       f'color:{NOIR}">{markdown(chr(10).join(q), depth + 1)}</blockquote>')
            continue
        it = _ITEM.match(ln)
        if it and depth < 6:
            flush()
            base = _indent(ln)
            ordered = it[2][0].isdigit()
            items: list[list[str]] = []
            while i < len(lines):
                cur = lines[i]
                m = _ITEM.match(cur)
                ind = _indent(cur)
                if m and ind == base:
                    if m[2][0].isdigit() != ordered:
                        break
                    items.append([m[3]])
                elif cur.strip() and ind > base and items:
                    items[-1].append(cur)
                elif not cur.strip() and items:
                    nxt = next((x for x in lines[i + 1:] if x.strip()), "")
                    nm = _ITEM.match(nxt)
                    if not (_indent(nxt) > base or (nm and _indent(nxt) == base and nm[2][0].isdigit() == ordered)):
                        break
                    items[-1].append("")
                else:
                    break
                i += 1
            tag = "ol" if ordered else "ul"
            start = ""
            if ordered:
                num = int(re.match(r"\d+", it[2])[0])
                start = f' start="{num}"' if num != 1 else ""
            lis = []
            for body in items:
                inner = markdown("\n".join(_dedent(body[1:])), depth + 1) if len(body) > 1 else ""
                lis.append(f'<li style="margin:0 0 4px;font-size:15px;line-height:1.65">{inline(body[0])}{inner}</li>')
            out.append(f'<{tag}{start} style="margin:0 0 12px;padding-left:24px">{"".join(lis)}</{tag}>')
            continue
        para.append(ln)
        i += 1
    flush()
    return "".join(out).replace("<p style=\"" + _P + "\"></p>", "")


def _indent(line: str) -> int:
    return len(line.expandtabs(4)) - len(line.expandtabs(4).lstrip()) if line.strip() else 0


def _dedent(lines: list[str]) -> list[str]:
    lines = [x.expandtabs(4) for x in lines]
    ind = min((_indent(x) for x in lines if x.strip()), default=0)
    return [x[ind:] for x in lines]


# ---------------------------------------------------------------- the mail
def subject(question: str | None, reply: str) -> str:
    """「[Agent J] <the input's first 40 characters>」 — the reply's first line when there is no input (or it is left out)."""
    src = question if (question or "").strip() else next((x.strip(" #*>-`") for x in reply.splitlines() if x.strip(" #*>-`")), "")
    one = " ".join(str(src or "").split())
    if len(one) > SUBJECT_CHARS:
        one = one[:SUBJECT_CHARS].rstrip() + "…"
    return f"[{BRAND}] {one}".strip()


def human(n: int) -> str:
    n = max(0, int(n or 0))
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n / 1024 / 1024:.1f} MB".replace(".0 ", " ")


def when(ts_ms) -> str:
    if not ts_ms:
        return ""
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts_ms / 1000))


def _label(L: dict, src: dict, agent: str) -> tuple[str, str]:
    """(chip text, the meta line) for the input card."""
    k = src.get("k") or "phone"
    meta = []
    if k in ("phone", "host"):
        chip = L["asked"]
        if src.get("name"):
            meta.append(L["from"].format(name=src["name"]))
    else:
        chip = L["kind"].get(k, L["asked"]).format(agent=agent)
        if k in ("telegram", "email"):
            chip = L["asked"]
            meta.append(L["from"].format(name=L["kind"][k]))
    return chip, " · ".join(meta)


def render(*, lang: str, agent: str, src: dict | None, reply: str, files=(), skipped=()) -> tuple[str, str, str]:
    """→ (subject, plain text, HTML). `src` = the page's input (history Src: k, name, text, quote?, att?, ts) or None to
    leave it out; `files` = [(name, bytes)] attached; `skipped` = [(name, bytes, why: too_big|gone|held)] not attached
    (P295 held = kept back by the strict scan for someone else: the mail never says why).
    Texts must already be redacted by the caller."""
    L = T["en" if lang == "en" else "zh"]
    agent = (agent or BRAND).strip() or BRAND
    q = src if src and (src.get("text") or "").strip() else None
    subj = subject(q["text"] if q else None, reply)

    # ---- plain text
    tx: list[str] = []
    if q:
        chip, meta = _label(L, q, agent)
        tx.append(" · ".join(x for x in (chip, meta, when(q.get("ts"))) if x))
        qt = q.get("quote") if isinstance(q.get("quote"), dict) else None
        if qt and qt.get("text"):
            tx.append("> " + L["quote"].format(who=qt.get("who") or "") + " ".join(str(qt["text"]).split()))
        tx += ["", q["text"].strip()]
        att = [a.get("name") for a in q.get("att") or [] if isinstance(a, dict) and a.get("name")]
        if att:
            tx += ["", L["sent_files"] + "、".join(att) if lang != "en" else L["sent_files"] + ", ".join(att)]
        tx += ["", "─" * 24, ""]
    tx += [L["answered"].format(agent=agent), "", reply.strip()]
    if files or skipped:
        tx += ["", L["files"] + ":"]
        tx += [f"- {n} ({human(b)})" for n, b in files]
        tx += [f"- {n} ({human(b)}) — {L.get(why, L['gone'])}" for n, b, why in skipped]
    tx += ["", "-- ", f"{L['foot'].format(agent=agent)}", f"{L['powered']} · {SITE}"]
    text = "\n".join(tx) + "\n"

    # ---- HTML
    def chip(label: str, bg: str) -> str:
        return (f'<span style="display:inline-block;background:{bg};color:{SOOTY};border-radius:999px;padding:3px 11px;'
                f'font-family:{BODY_FONT};font-size:12px;font-weight:700;line-height:1.4;letter-spacing:.02em">{esc(label)}</span>')

    def card(head: str, body: str, meta: str = "") -> str:
        m = f'<span class="aj-sub" style="color:{ARGENT};font-size:12px;line-height:1.4">&nbsp;&nbsp;{esc(meta)}</span>' if meta else ""
        return (f'<tr><td class="aj-pad" style="padding:0 24px 16px">'
                f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" class="aj-card" '
                f'style="background:{LOTION};border:1px solid {STRAW};border-radius:16px;border-collapse:separate;table-layout:fixed">'
                f'<tr><td class="aj-tx aj-in" style="padding:18px 20px 8px;color:{SOOTY};font-family:{BODY_FONT}">'
                f'<div style="margin:0 0 12px">{head}{m}</div>{body}</td></tr></table></td></tr>')
    parts = []
    if q:
        c, meta = _label(L, q, agent)
        meta = " · ".join(x for x in (meta, when(q.get("ts"))) if x)
        qt = q.get("quote") if isinstance(q.get("quote"), dict) else None
        qhtml = ""
        if qt and qt.get("text"):
            qhtml = (f'<div class="aj-q aj-qb" style="margin:0 0 12px;padding:6px 12px;border-left:3px solid {STRAW};'
                     f'background:{SALT};border-radius:0 8px 8px 0;color:{NOIR};font-size:13px;line-height:1.5">'
                     f'{esc(L["quote"].format(who=qt.get("who") or ""))}{esc(" ".join(str(qt["text"]).split()))}</div>')
        att = [a.get("name") for a in q.get("att") or [] if isinstance(a, dict) and a.get("name")]
        ahtml = (f'<p class="aj-sub" style="margin:0 0 12px;color:{ARGENT};font-size:13px">{esc(L["sent_files"])}'
                 f'{esc(("、" if lang != "en" else ", ").join(att))}</p>') if att else ""
        parts.append(card(chip(c, MARROW), qhtml + markdown(q["text"]) + ahtml, meta))
    fl = ""
    if files or skipped:
        rows = "".join(f'<tr><td class="aj-tx" style="padding:6px 0;font-size:14px;color:{SOOTY};border-top:1px dashed {STRAW}">'
                       f'&#128206;&nbsp;{esc(n)}<span class="aj-sub" style="color:{ARGENT}">&nbsp;·&nbsp;{esc(human(b))}</span></td></tr>'
                       for n, b in files)
        rows += "".join(f'<tr><td class="aj-tx" style="padding:6px 0;font-size:14px;color:{SOOTY};border-top:1px dashed {STRAW}">'
                        f'{esc(n)}<span class="aj-sub" style="color:{ARGENT}">&nbsp;·&nbsp;{esc(human(b))}&nbsp;·&nbsp;'
                        f'{esc(L.get(why, L["gone"]))}</span></td></tr>' for n, b, why in skipped)
        fl = (f'<div style="margin:4px 0 12px"><div class="aj-sub" style="color:{ARGENT};font-size:12px;font-weight:700;'
              f'letter-spacing:.06em;text-transform:uppercase;margin:0 0 4px">{esc(L["files"])}</div>'
              f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{rows}</table></div>')
    parts.append(card(chip(L["answered"].format(agent=agent), MOON), markdown(reply) + fl))

    pre = esc(" ".join((q["text"] if q else reply).split())[:120])
    doc = (
        f'<!DOCTYPE html><html lang="{L["lang"]}" xmlns="http://www.w3.org/1999/xhtml"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="x-apple-disable-message-reformatting">'
        '<meta name="color-scheme" content="light dark"><meta name="supported-color-schemes" content="light dark">'
        f"<title>{esc(subj)}</title><style>{_DARK_CSS}</style></head>"
        f'<body class="aj-bg" style="margin:0;padding:0;background:{LOTION};-webkit-text-size-adjust:100%">'
        f'<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:{LOTION}">{pre}</div>'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" class="aj-bg" bgcolor="{LOTION}" '
        f'style="background:{LOTION}"><tr><td align="center" style="padding:24px 8px">'
        f'<table role="presentation" class="aj-col" width="600" cellpadding="0" cellspacing="0" border="0" '
        f'style="width:600px;max-width:600px;table-layout:fixed">'
        f'<tr><td class="aj-pad aj-tx" style="padding:0 24px 14px;font-family:{TITLE_FONT};font-size:18px;font-weight:700;'
        f'color:{SOOTY};letter-spacing:.01em">{esc(agent)}</td></tr>'
        f'<tr><td class="aj-pad" style="padding:0 24px 18px"><div class="aj-seam" style="border-top:1.5px dashed {STRAW};'
        f'height:0;line-height:0;font-size:0">&nbsp;</div></td></tr>'
        + "".join(parts) +
        f'<tr><td class="aj-pad" style="padding:8px 24px 0"><table role="presentation" width="100%" cellpadding="0" '
        f'cellspacing="0" border="0" bgcolor="{SOOTY}" class="aj-foot" style="background:{SOOTY};border-radius:16px">'
        f'<tr><td class="aj-foot" style="padding:16px 20px;border-top:1px dashed {LOTION};border-radius:16px;font-family:{BODY_FONT};'
        f'font-size:12px;line-height:1.6;color:{LOTION}">{esc(L["foot"].format(agent=agent))}<br>'
        f'<a href="{SITE}" style="color:{LOTION};text-decoration:underline;text-decoration-color:{WINDSTORM};'
        f'font-weight:700">{esc(L["powered"])}</a></td></tr></table></td></tr>'
        "</table></td></tr></table></body></html>"
    )
    return subj, text, doc
