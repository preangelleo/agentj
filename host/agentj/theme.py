"""P169 外观主题: the phone's personalisable look as one folder (theme.json + PNG files), checked before it is used.

What a theme can change (schema: themes/theme.schema.json, JSON Schema draft 2020-12, shipped in the package and at
https://agentj.app/schema/agentj-theme-2.json): the five state colours of the chat screen, the five message-card fills,
the menu pages' colours (P187: background, panel, text, accent, line — readable or refused), the logo at the top left of
the chat page, the glyph on Send, and the background-work indicator (dancing robots with
your own sprite sheets, or the round dots with your own colours). What it cannot (x-agentj-locked): the pairing, safety
code, security, offline and removed screens, the 网页版 badge, the home-screen icon, fonts, every agentj.app page.

`agentj theme validate <folder>` = the schema (a small built-in subset validator: type, const, enum, pattern, length,
items, required, properties, additionalProperties false, local $ref) + the file rules below. `apply` re-checks a private
copy, puts the current theme to sleep (dormant/<when>-<id>/, newest DORMANT_KEEP kept), then switches; `restore <entry>`
wakes a dormant one (the current goes to sleep); `reset` = the factory robots. Two factory presets ship: robots (default,
P154) and dots (P150). Storage: <state>/agentperm/theme/ (the part of the state dir a fenced Agent sees, like busy items),
so the owner's Agent can run the whole loop from its sandbox. serve reads current.json every 2 s and sends §23
`{"t":"theme",…}` to every ready phone when it changed (the page repaints at once; a factory theme carries no files —
the page has them). serve validates again before sending: a broken active theme falls back to the factory robots.
Display only: a theme never reaches the approval path, and the page treats every field as untrusted (web js/theme.js).
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import json
import os
import pathlib
import secrets
import shutil
import struct
import sys
import time
import zlib

PKG = pathlib.Path(__file__).resolve().parent / "themes"
SCHEMA_PATH = PKG / "theme.schema.json"
FACTORY = ("robots", "dots")
DEFAULT = "robots"
DORMANT_KEEP = 20
THEME_JSON_MAX = 16 * 1024
TOTAL_MAX = 512 * 1024
LOTION, NOIR = "#faf9f5", "#2c2b26"
STATE_MIN_CONTRAST = 2.5        # Lotion text on the state colour (factory lowest: Windstorm 2.78)
CARD_MIN_CONTRAST = 4.5         # Noir text on a card fill
MENU_MIN_CONTRAST = 4.5          # P187: menu text (and the 72 % muted text) on the menu background and panel
MENU_ACCENT_MIN_CONTRAST = 3.0   # the accent is a non-text mark (switch on, selection, focus ring)
MENU_MUTED_ALPHA = 0.72          # web menu.css --menu-muted
STATE_MIN_DISTANCE = 30         # RGB distance between any two state colours (factory closest: Windstorm / Magic Carpet 41)
DOT_MIN_DISTANCE = 24           # heavy ⇄ light of one dot, so the blink shows
FRAMES, CELL_SMALL, CELL_BIG = 13, 12, 18     # 12 dance steps + the bow; 12×12 art (shown 24 px) and 18×18 art (shown 18 px)
ROBOT_MAX_COLOURS, FRAME_MIN_PIXELS = 8, 6
SQUARE_SIZES = (128, 256)
FILE_MAX = {"logo": 64 * 1024, "send_icon": 32 * 1024, "sheet": 32 * 1024}
SEND_COVER = (0.05, 0.70)
LOGO_COVER_MIN = 0.10
KNOWN_EXTRA = {"README.md", "meta.json"}


# ---------------------------------------------------------------- messages
def _m(code: str, path: str, zh: str, en: str) -> dict:
    return {"code": code, "path": path, "zh": zh, "en": en}


# ---------------------------------------------------------------- PNG (8-bit, non-interlaced; no third-party library)
class PngError(ValueError):
    pass


def read_png(data: bytes, max_side: int = 1024):
    """→ (width, height, rows, has_alpha, animated); rows = list of bytes, 4 per pixel (RGBA). Raises PngError."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise PngError("not_png")
    pos, ihdr, plte, trns, idat, animated = 8, None, None, None, [], False
    while pos + 12 <= len(data):
        ln = int.from_bytes(data[pos:pos + 4], "big")
        typ, body = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + ln]
        crc = data[pos + 8 + ln:pos + 12 + ln]
        if len(body) != ln or len(crc) != 4 or zlib.crc32(typ + body) != int.from_bytes(crc, "big"):
            raise PngError("corrupt")
        pos += 12 + ln
        if typ == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", body)
        elif typ == b"PLTE":
            plte = body
        elif typ == b"tRNS":
            trns = body
        elif typ == b"IDAT":
            idat.append(body)
        elif typ == b"acTL":
            animated = True
        elif typ == b"IEND":
            break
    if not ihdr or not idat:
        raise PngError("corrupt")
    w, h, depth, ctype, _comp, _flt, interlace = ihdr
    if depth != 8 or ctype not in (2, 3, 4, 6) or interlace:
        raise PngError("format")
    if not (0 < w <= max_side and 0 < h <= max_side):
        raise PngError("too_big")
    bpp = {2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    stride = w * bpp
    want = h * (stride + 1)
    d = zlib.decompressobj()
    try:
        raw = d.decompress(b"".join(idat), want + 1)
    except zlib.error as e:
        raise PngError("corrupt") from e
    if len(raw) != want:
        raise PngError("corrupt")
    rows, prev = [], bytearray(stride)
    for y in range(h):
        f, line = raw[y * (stride + 1)], bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        if f == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 255
        elif f == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 255
        elif f == 3:
            for i in range(stride):
                line[i] = (line[i] + ((line[i - bpp] if i >= bpp else 0) + prev[i]) // 2) & 255
        elif f == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b, c = prev[i], (prev[i - bpp] if i >= bpp else 0)
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        elif f != 0:
            raise PngError("corrupt")
        rows.append(line)
        prev = line
    out, has_alpha = [], ctype in (4, 6) or (ctype == 3 and trns is not None)
    for line in rows:
        if ctype == 6:
            out.append(bytes(line))
            continue
        px = bytearray()
        for x in range(w):
            if ctype == 2:
                px += line[x * 3:x * 3 + 3] + b"\xff"
            elif ctype == 4:
                g, a = line[x * 2], line[x * 2 + 1]
                px += bytes((g, g, g, a))
            else:
                i = line[x]
                if not plte or 3 * i + 3 > len(plte):
                    raise PngError("corrupt")
                px += plte[3 * i:3 * i + 3] + bytes((trns[i] if trns and i < len(trns) else 255,))
        out.append(bytes(px))
    return w, h, out, has_alpha, animated


# ---------------------------------------------------------------- colour
def _rgb(h: str):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def _lum(h: str) -> float:
    c = [v / 255 for v in _rgb(h)]
    c = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def contrast(a: str, b: str) -> float:
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def blend(fg: str, alpha: float, bg: str) -> str:
    """fg at `alpha` over bg (#rrggbb) — what the page paints for color-mix(fg alpha%, transparent) on that surface."""
    f, b = _rgb(fg), _rgb(bg)
    return "#" + "".join(f"{round(x * alpha + y * (1 - alpha)):02x}" for x, y in zip(f, b))


def distance(a: str, b: str) -> float:
    return sum((x - y) ** 2 for x, y in zip(_rgb(a), _rgb(b))) ** 0.5


# ---------------------------------------------------------------- schema (subset of draft 2020-12)
SCHEMA_KEYWORDS = {"oneOf", "$schema", "$id", "$ref", "$defs", "title", "description", "default", "type", "const", "enum", "pattern",
                   "minLength", "maxLength", "minItems", "maxItems", "items", "required", "properties",
                   "additionalProperties", "x-agentj-locked"}


def schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _schema_errors(value, node: dict, root: dict, path: str, out: list) -> None:
    import re
    if "$ref" in node:
        ref = node["$ref"]
        assert ref.startswith("#/$defs/"), ref
        _schema_errors(value, root["$defs"][ref.split("/")[-1]], root, path, out)
        node = {k: v for k, v in node.items() if k != "$ref"}
    where = path or "theme.json"
    t = node.get("type")
    if "oneOf" in node:
        matches = 0
        for branch in node["oneOf"]:
            branch_errors = []
            _schema_errors(value, branch, root, path, branch_errors)
            matches += not branch_errors
        if matches != 1:
            out.append(_m("schema.oneOf", where, "image 和 states 必须二选一", "choose exactly one of image or states"))
    types = {"object": dict, "string": str, "array": list, "boolean": bool}
    if t == "integer":
        ok = isinstance(value, int) and not isinstance(value, bool)
    else:
        ok = t is None or (isinstance(value, types[t]) and not (t != "boolean" and isinstance(value, bool)))
    if not ok:
        out.append(_m("schema.type", where, f"应该是 {t}", f"must be a {t}"))
        return
    if "const" in node and value != node["const"]:
        out.append(_m("schema.const", where, f"必须是 {json.dumps(node['const'])}", f"must be {json.dumps(node['const'])}"))
    if "enum" in node and value not in node["enum"]:
        opts = " | ".join(map(str, node["enum"]))
        out.append(_m("schema.enum", where, f"只能是 {opts}", f"must be one of {opts}"))
    if isinstance(value, str):
        if "pattern" in node and not re.search(node["pattern"], value):
            out.append(_m("schema.pattern", where, f"格式不对（要符合 {node['pattern']}）", f"wrong format (must match {node['pattern']})"))
        if len(value) < node.get("minLength", 0):
            out.append(_m("schema.minLength", where, "不能为空", "must not be empty"))
        if "maxLength" in node and len(value) > node["maxLength"]:
            out.append(_m("schema.maxLength", where, f"最多 {node['maxLength']} 个字", f"at most {node['maxLength']} characters"))
    if isinstance(value, list):
        if len(value) < node.get("minItems", 0):
            out.append(_m("schema.minItems", where, f"至少 {node['minItems']} 项", f"at least {node['minItems']} items"))
        if "maxItems" in node and len(value) > node["maxItems"]:
            out.append(_m("schema.maxItems", where, f"最多 {node['maxItems']} 项", f"at most {node['maxItems']} items"))
        if "items" in node:
            for i, v in enumerate(value):
                _schema_errors(v, node["items"], root, f"{path}[{i}]", out)
    if isinstance(value, dict):
        for k in node.get("required", ()):
            if k not in value:
                out.append(_m("schema.required", f"{path}.{k}" if path else k, "缺这一项", "missing"))
        props = node.get("properties", {})
        for k, v in value.items():
            sub = f"{path}.{k}" if path else k
            if k in props:
                _schema_errors(v, props[k], root, sub, out)
            elif node.get("additionalProperties") is False:
                out.append(_m("schema.unknown", sub, "不认识这一项（拼错了？）", "unknown field (a typo?)"))


def schema_errors(doc) -> list:
    out: list = []
    s = schema()
    _schema_errors(doc, s, s, "", out)
    return out


# ---------------------------------------------------------------- folder validation
def _png_rule(folder: pathlib.Path, name: str, where: str, kind: str, out: list, files: dict):
    """Read one referenced PNG (inside the folder, a regular file, size limit) → decoded or None (errors appended)."""
    p = folder / name
    if p.is_symlink() or not p.is_file():
        out.append(_m("file.missing", where, f"找不到文件 {name}（要放在 theme.json 旁边，不能是链接）",
                      f"file {name} not found (it must sit next to theme.json, not a link)"))
        return None
    data = p.read_bytes()
    lim = FILE_MAX[kind]
    if len(data) > lim:
        out.append(_m("file.size", where, f"{name} 太大：{len(data) // 1024} KiB，上限 {lim // 1024} KiB",
                      f"{name} is too large: {len(data) // 1024} KiB, the limit is {lim // 1024} KiB"))
        return None
    try:
        img = read_png(data)
    except PngError as e:
        why = {"not_png": ("不是 PNG 文件", "is not a PNG file"), "format": ("要 8 位、不隔行的 PNG（RGBA 最好）", "must be an 8-bit, non-interlaced PNG (RGBA is best)"),
               "too_big": ("尺寸太大", "is too large"), "corrupt": ("文件损坏", "is damaged")}[str(e)]
        out.append(_m("png." + str(e), where, f"{name} {why[0]}", f"{name} {why[1]}"))
        return None
    if img[4]:
        out.append(_m("png.animated", where, f"{name} 是动图 PNG：只要静态图（机器人的动画靠 13 帧精灵图）",
                      f"{name} is an animated PNG: only still images (robots animate through the 13-frame sheet)"))
        return None
    files[name] = data
    return img


def _square(img, name, where, out) -> bool:
    w, h = img[0], img[1]
    if w != h or w not in SQUARE_SIZES:
        out.append(_m("png.size", where, f"{name} 必须是 128×128 或 256×256 像素（现在 {w}×{h}）",
                      f"{name} must be 128×128 or 256×256 px (now {w}×{h})"))
        return False
    return True


def _coverage(img) -> float:
    w, h, rows = img[0], img[1], img[2]
    return sum(1 for r in rows for x in range(w) if r[4 * x + 3] >= 128) / (w * h)


def _check_sheet(img, name, where, cell, n, out) -> None:
    w, h, rows = img[0], img[1], img[2]
    if (w, h) != (FRAMES * cell, n * cell):
        out.append(_m("robots.size", where,
                      f"{name} 必须是 {FRAMES * cell}×{n * cell} 像素（{FRAMES} 帧 × {cell} 像素宽，{n} 个机器人 × {cell} 像素高；现在 {w}×{h}）",
                      f"{name} must be {FRAMES * cell}×{n * cell} px ({FRAMES} frames × {cell} px across, {n} robots × {cell} px down; now {w}×{h})"))
        return
    soft = sum(1 for r in rows for x in range(w) if 0 < r[4 * x + 3] < 255)
    if soft:
        out.append(_m("robots.edges", where, f"{name} 有 {soft} 个半透明像素：像素画只能全透明或全不透明（关掉抗锯齿）",
                      f"{name} has {soft} half-transparent pixels: pixel art is fully clear or fully opaque (turn off anti-aliasing)"))
    for r in range(n):
        colours = {rows[y][4 * x:4 * x + 3] for y in range(r * cell, (r + 1) * cell) for x in range(w) if rows[y][4 * x + 3] == 255}
        if len(colours) > ROBOT_MAX_COLOURS:
            out.append(_m("robots.colours", where, f"{name} 第 {r + 1} 个机器人用了 {len(colours)} 种颜色，最多 {ROBOT_MAX_COLOURS} 种",
                          f"{name} robot {r + 1} uses {len(colours)} colours, at most {ROBOT_MAX_COLOURS}"))
        for f in range(FRAMES):
            px = sum(1 for y in range(r * cell, (r + 1) * cell) for x in range(f * cell, (f + 1) * cell) if rows[y][4 * x + 3])
            if px < FRAME_MIN_PIXELS:
                step = "鞠躬帧" if f == FRAMES - 1 else f"第 {f + 1} 帧"
                out.append(_m("robots.empty", where, f"{name} 第 {r + 1} 个机器人的{step}是空的（至少 {FRAME_MIN_PIXELS} 个像素）",
                              f"{name} robot {r + 1}, {'the bow frame' if f == FRAMES - 1 else f'frame {f + 1}'} is empty (at least {FRAME_MIN_PIXELS} pixels)"))
                break


def _check_colours(doc, out) -> None:
    c = doc.get("colors") or {}
    st = c.get("state") or {}
    for k, v in st.items():
        if contrast(v, LOTION) < STATE_MIN_CONTRAST:
            out.append(_m("color.contrast", f"colors.state.{k}",
                          f"{v} 太浅：上面的白字（Lotion #faf9f5）对比度只有 {contrast(v, LOTION):.2f}:1，至少 {STATE_MIN_CONTRAST}:1，调深一点",
                          f"{v} is too light: the Lotion #faf9f5 text on it is only {contrast(v, LOTION):.2f}:1, needs {STATE_MIN_CONTRAST}:1 — go darker"))
    full = {**{k: schema()["properties"]["colors"]["properties"]["state"]["properties"][k]["default"] for k in ("idle", "working", "waiting", "question", "offline")}, **st}
    ks = list(full)
    for i, a in enumerate(ks):
        for b in ks[i + 1:]:
            if (a in st or b in st) and distance(full[a], full[b]) < STATE_MIN_DISTANCE:
                out.append(_m("color.distinct", f"colors.state.{a}",
                              f"{a} {full[a]} 和 {b} {full[b]} 太像了，一眼分不出状态",
                              f"{a} {full[a]} and {b} {full[b]} look too alike to tell the states apart"))
    for k, v in (c.get("card") or {}).items():
        if contrast(v, NOIR) < CARD_MIN_CONTRAST:
            out.append(_m("color.contrast", f"colors.card.{k}",
                          f"{v} 太深：卡片上的深色字（Noir #2c2b26）对比度只有 {contrast(v, NOIR):.2f}:1，至少 {CARD_MIN_CONTRAST}:1，调浅一点",
                          f"{v} is too dark: the Noir #2c2b26 text on it is only {contrast(v, NOIR):.2f}:1, needs {CARD_MIN_CONTRAST}:1 — go lighter"))
    menu = c.get("menu") or {}
    if menu:
        props = schema()["properties"]["colors"]["properties"]["menu"]["properties"]
        full = {k: menu.get(k, props[k].get("default")) for k in ("background", "panel", "text", "accent")}
        for under in ("background", "panel"):
            for what, fg in (("text", full["text"]), ("muted", blend(full["text"], MENU_MUTED_ALPHA, full[under]))):
                k = contrast(fg, full[under])
                if k < MENU_MIN_CONTRAST:
                    zh_what = "正文" if what == "text" else "说明文字（正文颜色 72%）"
                    zh_under = "底色" if under == "background" else "分组底色"
                    en_what = "text" if what == "text" else "notes (the text colour at 72 %)"
                    out.append(_m("color.contrast", "colors.menu." + ("text" if "text" in menu else under),
                                  f"菜单{zh_what}在{zh_under} {full[under]} 上对比度只有 {k:.2f}:1，至少 {MENU_MIN_CONTRAST}:1",
                                  f"menu {en_what} on the {under} {full[under]} is only {k:.2f}:1, needs {MENU_MIN_CONTRAST}:1"))
        k = contrast(full["accent"], full["background"])
        if k < MENU_ACCENT_MIN_CONTRAST:
            out.append(_m("color.contrast", "colors.menu.accent",
                          f"强调色 {full['accent']} 在菜单底色上对比度只有 {k:.2f}:1，开关和选中标记看不清，至少 {MENU_ACCENT_MIN_CONTRAST}:1",
                          f"the accent {full['accent']} on the menu background is only {k:.2f}:1 — switches and the selection would not show; needs {MENU_ACCENT_MIN_CONTRAST}:1"))
    dlg = (c.get("dialog") or {}).get("background")     # P207: the one centred-dialog background, Lotion text on it
    if dlg and contrast(dlg, LOTION) < MENU_MIN_CONTRAST:
        out.append(_m("color.contrast", "colors.dialog.background",
                      f"{dlg} 太浅：弹窗上的白字（Lotion #faf9f5）对比度只有 {contrast(dlg, LOTION):.2f}:1，至少 {MENU_MIN_CONTRAST}:1，调深一点",
                      f"{dlg} is too light: the Lotion #faf9f5 text on it is only {contrast(dlg, LOTION):.2f}:1, needs {MENU_MIN_CONTRAST}:1 — go darker"))
    for i, p in enumerate((((doc.get("busy") or {}).get("dots") or {}).get("colors")) or []):
        if distance(p["heavy"], p["light"]) < DOT_MIN_DISTANCE:
            out.append(_m("color.blink", f"busy.dots.colors[{i}]", f"heavy 和 light 太像，闪烁看不出来",
                          "heavy and light are too alike — the blink would not show"))


def validate(folder, factory_ok: bool = False) -> dict:
    """→ {"ok", "errors": [...], "warnings": [...], "theme": doc|None, "files": {name: bytes}} — never raises."""
    folder = pathlib.Path(folder)
    errors, warnings, files = [], [], {}
    res = {"ok": False, "errors": errors, "warnings": warnings, "theme": None, "files": files}
    if folder.is_symlink() or not folder.is_dir():
        errors.append(_m("dir.missing", str(folder), "不是一个文件夹（主题 = 一个文件夹：theme.json + PNG 图）",
                         "not a folder (a theme is a folder: theme.json + PNG images)"))
        return res
    tj = folder / "theme.json"
    if tj.is_symlink() or not tj.is_file():
        errors.append(_m("json.missing", "theme.json", "文件夹里没有 theme.json", "no theme.json in the folder"))
        return res
    raw = tj.read_bytes()
    if len(raw) > THEME_JSON_MAX:
        errors.append(_m("json.size", "theme.json", f"theme.json 太大（上限 {THEME_JSON_MAX // 1024} KiB）", f"theme.json is too large (limit {THEME_JSON_MAX // 1024} KiB)"))
        return res
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        errors.append(_m("json.syntax", "theme.json", f"不是合法的 JSON：{e}", f"not valid JSON: {e}"))
        return res
    errors += schema_errors(doc)
    if errors:
        return res
    res["theme"] = doc
    if doc["id"] in FACTORY and not factory_ok:
        errors.append(_m("id.reserved", "id", f"{doc['id']} 是出厂主题的名字，换一个", f"{doc['id']} is a factory theme's name — pick another"))
    _check_colours(doc, errors)
    if doc.get("logo"):
        logo = doc["logo"]
        refs = logo.get("states") or {"image": logo["image"]}
        sizes = set()
        for state, filename in refs.items():
            where = "logo.image" if state == "image" else f"logo.states.{state}"
            img = _png_rule(folder, filename, where, "logo", errors, files)
            if not img or not _square(img, filename, where, errors):
                continue
            sizes.add(img[:2])
            if _coverage(img) < LOGO_COVER_MIN:
                errors.append(_m("logo.empty", where, "logo 几乎是空的（不到 10%）", "logo is almost empty (under 10%)"))
            if state != "image":
                bg = (doc.get("colors", {}).get("state", {}).get(state)
                      or schema()["properties"]["colors"]["properties"]["state"]["properties"][state]["default"])
                # Same rule in light and dark mode. Disc is opaque Lotion in the page.
                for background in (bg, blend("#141414", .28, bg)):
                    under = LOTION if logo.get("disc", True) else background
                    pixels = [tuple(row[x:x+4]) for row in img[2] for x in range(0, len(row), 4)]
                    visible = [px for px in pixels if px[3]]
                    readable = sum(contrast(blend("#%02x%02x%02x" % px[:3], px[3]/255, under), under) >= STATE_MIN_CONTRAST for px in visible)
                    if (logo.get("disc", True) and contrast(LOTION, background) < 2.5) or readable < len(pixels) * .10:
                        errors.append(_m("logo.contrast", where, "logo 在对应背景上不够清楚（至少 10% 像素对比度 2.5:1）",
                                         "logo needs at least 10% readable pixels at 2.5:1 on its state background"))
                        break
        if len(sizes) > 1:
            errors.append(_m("logo.dimensions", "logo.states", "五张 logo 必须同尺寸", "all five logos must have the same dimensions"))
    if doc.get("send_icon"):
        name = doc["send_icon"]["image"]
        img = _png_rule(folder, name, "send_icon.image", "send_icon", errors, files)
        if img and _square(img, name, "send_icon.image", errors):
            w, rows = img[0], img[2]
            if not img[3]:
                errors.append(_m("send.alpha", "send_icon.image", f"{name} 没有透明通道：发送图标只用形状，背景必须透明",
                                 f"{name} has no transparency: only the shape is used, the background must be clear"))
            else:
                corners = [rows[y][4 * x + 3] for y in (0, w - 1) for x in (0, w - 1)]
                cov = _coverage(img)
                if any(corners):
                    errors.append(_m("send.corners", "send_icon.image", f"{name} 的四个角必须透明（现在会显示成一个方块）",
                                     f"{name} must have clear corners (now it shows as a square)"))
                if not SEND_COVER[0] <= cov <= SEND_COVER[1]:
                    errors.append(_m("send.cover", "send_icon.image", f"{name} 覆盖了 {cov:.0%}：要在 5%–70% 之间，按钮上才认得出",
                                     f"{name} covers {cov:.0%}: it must be 5–70 % to read on the button"))
    rb = (doc.get("busy") or {}).get("robots")
    if rb:
        if doc["busy"]["style"] != "robots":
            warnings.append(_m("robots.unused", "busy.robots", "style 是 dots，robots 这一节不会用到", "style is dots, so busy.robots is not used"))
        n = len(rb["names"])
        for key, cell in (("sheet_12", CELL_SMALL), ("sheet_18", CELL_BIG)):
            img = _png_rule(folder, rb[key], f"busy.robots.{key}", "sheet", errors, files)
            if img:
                _check_sheet(img, rb[key], f"busy.robots.{key}", cell, n, errors)
    if (doc.get("busy") or {}).get("dots") and doc["busy"]["style"] != "dots":
        warnings.append(_m("dots.unused", "busy.dots", "style 是 robots，dots 这一节不会用到", "style is robots, so busy.dots is not used"))
    if sum(map(len, files.values())) + len(raw) > TOTAL_MAX:
        errors.append(_m("total.size", "", f"整个主题超过 {TOTAL_MAX // 1024} KiB", f"the theme is over {TOTAL_MAX // 1024} KiB in total"))
    for p in sorted(folder.iterdir()):
        if p.name != "theme.json" and p.name not in files and p.name not in KNOWN_EXTRA:
            warnings.append(_m("file.extra", p.name, f"{p.name} 没被 theme.json 用到，apply 时不会复制", f"{p.name} is not used by theme.json and is not copied on apply"))
    res["ok"] = not errors
    return res


def rev(doc: dict, files: dict) -> str:
    h = hashlib.sha256(json.dumps(doc, sort_keys=True, ensure_ascii=False).encode())
    for k in sorted(files):
        h.update(k.encode() + b"\0" + hashlib.sha256(files[k]).digest())
    return h.hexdigest()[:16]


# ---------------------------------------------------------------- storage
def home(root) -> pathlib.Path:
    return pathlib.Path(root) / "agentperm" / "theme"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _write_json(path: pathlib.Path, obj) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(3)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def current(root) -> dict:
    """{"kind": "factory"|"custom", "id", "name"?, "rev"?, "applied_at"?}; no file / unreadable → the factory default."""
    with contextlib.suppress(OSError, ValueError):
        c = json.loads((home(root) / "current.json").read_text(encoding="utf-8"))
        if isinstance(c, dict) and c.get("kind") == "factory" and c.get("id") in FACTORY:
            return {"name": _preset_name(c["id"]), **c}
        if isinstance(c, dict) and c.get("kind") == "custom" and isinstance(c.get("id"), str):
            return c
    return {"kind": "factory", "id": DEFAULT, "name": _preset_name(DEFAULT)}


def preset(pid: str) -> pathlib.Path:
    return PKG / pid


def _preset_name(pid: str) -> dict:
    with contextlib.suppress(OSError, ValueError, KeyError):
        return json.loads((preset(pid) / "theme.json").read_text(encoding="utf-8"))["name"]
    return {"zh": pid, "en": pid}


def _ensure(root) -> pathlib.Path:
    h = home(root)
    (h / "dormant").mkdir(mode=0o700, parents=True, exist_ok=True)
    return h


def _sleep_current(root) -> str | None:
    """Put the current theme to sleep → the dormant entry's name. A factory theme (also the never-applied default) gets
    an entry with meta.json only, so `restore` can go back to it as well."""
    h, cur = _ensure(root), current(root)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    name = f"{stamp}-{cur['id']}"
    k = 2
    while (h / "dormant" / name).exists():
        name, k = f"{stamp}-{cur['id']}-{k}", k + 1
    entry = h / "dormant" / name
    if cur["kind"] == "custom" and (h / "active").is_dir():
        os.replace(h / "active", entry)
    else:
        entry.mkdir(mode=0o700)
    _write_json(entry / "meta.json", {**cur, "dormant_at": _now(), "dormant_ns": time.time_ns()})
    _prune(h)
    return name


def _entries(h: pathlib.Path) -> list:
    """Dormant entries oldest first: (folder, meta) — by the time they went to sleep (names alone tie within a second)."""
    out = []
    d = h / "dormant"
    for p in (d.iterdir() if d.is_dir() else ()):
        meta = {}
        with contextlib.suppress(OSError, ValueError):
            meta = json.loads((p / "meta.json").read_text(encoding="utf-8"))
        if p.is_dir() and isinstance(meta, dict):
            out.append((p, meta))
    return sorted(out, key=lambda e: (e[1].get("dormant_ns") or 0, e[0].name))


def _prune(h: pathlib.Path) -> None:
    entries = _entries(h)
    for p, _m in entries[:-DORMANT_KEEP] if len(entries) > DORMANT_KEEP else []:
        shutil.rmtree(p, ignore_errors=True)


def dormant(root) -> list[dict]:
    """Newest first."""
    return [{"entry": p.name, **{k: m.get(k) for k in ("kind", "id", "name", "applied_at", "dormant_at")}}
            for p, m in reversed(_entries(home(root))) if m]


def apply_preset(root, pid: str) -> dict:
    if pid not in FACTORY:
        return {"ok": False, "error": "unknown_preset"}
    cur = current(root)
    if cur["kind"] == "factory" and cur["id"] == pid and (home(root) / "current.json").exists():
        return {"ok": True, "unchanged": True, "current": cur}
    slept = _sleep_current(root)
    h = home(root)
    if (h / "active").exists():
        shutil.rmtree(h / "active", ignore_errors=True)
    doc = json.loads((preset(pid) / "theme.json").read_text(encoding="utf-8"))
    new = {"kind": "factory", "id": pid, "name": doc["name"], "applied_at": _now()}
    _write_json(h / "current.json", new)
    return {"ok": True, "current": new, "dormant": slept}


def apply_dir(root, folder) -> dict:
    """Validate → private copy → validate the copy → current goes to sleep → switch. Nothing changes on any failure."""
    r = validate(folder)
    if not r["ok"]:
        return {"ok": False, "error": "invalid", "report": r}
    h = _ensure(root)
    if not r["theme"].get("logo") and current(root)["kind"] == "custom":
        old = validate(h / "active")
        if old["ok"] and old["theme"].get("logo"):
            logo = dict(old["theme"]["logo"])
            refs = logo.get("states") or {"image": logo["image"]}
            copied = {}
            for state, filename in refs.items():
                target = f"kept-logo-{state}.png"
                while target in r["files"]:
                    target = "k-" + target
                r["files"][target] = old["files"][filename]
                copied[state] = target
            if "states" in logo:
                logo["states"] = copied
                r["theme"]["schema"] = 2
            else:
                logo["image"] = copied["image"]
            r["theme"]["logo"] = logo
    new_rev = rev(r["theme"], r["files"])
    cur = current(root)
    if cur["kind"] == "custom" and cur.get("rev") == new_rev and (h / "active").is_dir():
        return {"ok": True, "unchanged": True, "current": cur}
    stage = h / f".staging-{secrets.token_hex(4)}"
    stage.mkdir(mode=0o700)
    try:
        (stage / "theme.json").write_text(json.dumps(r["theme"], ensure_ascii=False), encoding="utf-8")
        for name, data in r["files"].items():
            (stage / name).write_bytes(data)
        again = validate(stage)
        if not again["ok"] or rev(again["theme"], again["files"]) != new_rev:
            return {"ok": False, "error": "changed_while_copying", "report": again}
        slept = _sleep_current(root)
        if (h / "active").exists():
            shutil.rmtree(h / "active", ignore_errors=True)
        os.replace(stage, h / "active")
        new = {"kind": "custom", "id": r["theme"]["id"], "name": r["theme"]["name"], "rev": new_rev, "applied_at": _now()}
        _write_json(h / "current.json", new)
        return {"ok": True, "current": new, "dormant": slept, "warnings": r["warnings"]}
    finally:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)


def restore(root, entry: str) -> dict:
    h = home(root)
    if not entry or "/" in entry or entry.startswith("."):
        return {"ok": False, "error": "unknown_entry"}
    p = h / "dormant" / entry
    try:
        meta = json.loads((p / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"ok": False, "error": "unknown_entry"}
    if meta.get("kind") == "factory":
        r = apply_preset(root, meta.get("id"))
        if r.get("ok"):
            shutil.rmtree(p, ignore_errors=True)
        return r
    r = apply_dir(root, p)
    if r.get("ok"):
        shutil.rmtree(p, ignore_errors=True)
    return r


def export(root, which: str, dest) -> dict:
    """Copy a preset (robots / dots) or the active theme into a new folder to edit from."""
    dest = pathlib.Path(dest)
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        return {"ok": False, "error": "dest_not_empty"}
    if which in FACTORY:
        src = preset(which)
    elif which == "active" and current(root)["kind"] == "custom":
        src = home(root) / "active"
    elif which == "active":
        src = preset(current(root)["id"])
    else:
        return {"ok": False, "error": "unknown_preset"}
    r = validate(src, factory_ok=True)
    dest.mkdir(parents=True, exist_ok=True)
    doc = r["theme"]
    if src.parent == PKG:   # an edited copy must not keep the factory id
        doc = {**doc, "id": f"my-{doc['id']}", "author": "",
               "name": {"zh": ("我的" + doc["name"]["zh"])[:12], "en": ("My " + doc["name"]["en"])[:24]}}
    (dest / "theme.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for name, data in r["files"].items():
        (dest / name).write_bytes(data)
    return {"ok": True, "path": str(dest), "files": ["theme.json", *sorted(r["files"])]}


# ---------------------------------------------------------------- the phone message (PROTOCOL §23)
def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def phone_message(root) -> dict:
    cur = current(root)
    if cur["kind"] == "factory":
        return {"t": "theme", "v": 1, "id": cur["id"], "factory": cur["id"], "rev": cur["id"]}
    r = validate(home(root) / "active")
    if not r["ok"]:             # a damaged active theme never reaches a phone half-way: the factory default instead
        return {"t": "theme", "v": 1, "id": DEFAULT, "factory": DEFAULT, "rev": DEFAULT, "broken": True}
    doc, files = r["theme"], r["files"]
    m = {"t": "theme", "v": 1, "id": doc["id"], "factory": None, "rev": rev(doc, files), "name": doc["name"]}
    if doc.get("colors"):
        m["colors"] = doc["colors"]
    if doc.get("logo"):
        logo = doc["logo"]
        m["logo"] = {"disc": logo.get("disc", True)}
        if logo.get("states"):
            m["logo"]["states"] = {k: _b64(files[v]) for k, v in logo["states"].items()}
        else:
            m["logo"]["png"] = _b64(files[logo["image"]])
    if doc.get("send_icon"):
        m["send"] = {"png": _b64(files[doc["send_icon"]["image"]])}
    b = doc.get("busy")
    if b:
        if b["style"] == "robots" and b.get("robots"):
            rb = b["robots"]
            m["busy"] = {"style": "robots", "n": len(rb["names"]), "names": rb["names"],
                         "s12": _b64(files[rb["sheet_12"]]), "s18": _b64(files[rb["sheet_18"]])}
        elif b["style"] == "dots" and b.get("dots"):
            m["busy"] = {"style": "dots", "colors": [[p["heavy"], p["light"]] for p in b["dots"]["colors"]],
                         "blink": b["dots"].get("blink", True)}
        else:
            m["busy"] = {"style": b["style"]}
    return m


def stamp(root):
    """Cheap change detector for serve's 2-second loop."""
    p = home(root) / "current.json"
    with contextlib.suppress(OSError):
        s = p.stat()
        return (s.st_mtime_ns, s.st_size)
    return None


# ---------------------------------------------------------------- CLI
def _say(m: dict, mark: str) -> str:
    where = f"{m['path']}: " if m.get("path") else ""
    return f"{mark} {where}{m['zh']} / {m['en']}"


def _label(n) -> str:
    return f"{n.get('zh', '')} / {n.get('en', '')}" if isinstance(n, dict) else ""


def print_report(r: dict) -> None:
    for e in r["errors"]:
        print(_say(e, "✗"))
    for w in r["warnings"]:
        print(_say(w, "!"))
    if r["ok"]:
        doc = r["theme"]
        print(f"✓ {doc['id']} · {_label(doc['name'])}：全部通过，可以 apply / all checks passed — ready to apply")
    else:
        print(f"✗ {len(r['errors'])} 项没通过，改好再 validate；什么都没改 / {len(r['errors'])} failed — fix and validate again; nothing was changed")


def cmd_theme(a) -> int:
    from .state import state_dir
    root = state_dir()
    c = a.theme_cmd
    js = getattr(a, "json", False)
    if c == "schema":
        print(str(SCHEMA_PATH) if a.path else SCHEMA_PATH.read_text(encoding="utf-8"), end="" if not a.path else "\n")
        return 0
    if c == "list":
        cur, sleeping = current(root), dormant(root)
        presets = []
        for pid in FACTORY:
            doc = json.loads((preset(pid) / "theme.json").read_text(encoding="utf-8"))
            presets.append({"id": pid, "name": doc["name"], "default": pid == DEFAULT,
                            "active": cur["kind"] == "factory" and cur["id"] == pid})
        if js:
            print(json.dumps({"current": cur, "factory": presets, "dormant": sleeping}, ensure_ascii=False))
            return 0
        print("出厂 / factory:")
        for p in presets:
            tag = ("  （默认 / default）" if p["default"] else "") + ("  ← 正在用 / active" if p["active"] else "")
            print(f"  {'●' if p['active'] else '○'} {p['id']:<8} {_label(p['name'])}{tag}")
        if cur["kind"] == "custom":
            print(f"自定义 / custom:\n  ● {cur['id']}  {_label(cur.get('name'))}  ← 正在用 / active（{cur.get('applied_at', '')}）")
        print("休眠 / dormant（agentj theme restore <名字 / entry>）:" if sleeping else "休眠 / dormant: —")
        for d in sleeping:
            print(f"  {d['entry']}  {_label(d.get('name'))}")
        return 0
    if c == "show":
        which = a.which or "active"
        if which == "active":
            cur = current(root)
            folder = home(root) / "active" if cur["kind"] == "custom" else preset(cur["id"])
        elif which in FACTORY:
            cur, folder = {"kind": "factory", "id": which}, preset(which)
        else:
            cur, folder = {"kind": "folder", "id": which}, pathlib.Path(which)
        r = validate(folder, factory_ok=folder.parent == PKG)
        out = {"current": cur, "theme": r["theme"], "files": {k: len(v) for k, v in r["files"].items()}, "ok": r["ok"]}
        if js:
            print(json.dumps(out, ensure_ascii=False))
            return 0 if r["ok"] else 1
        if not r["theme"]:
            print_report(r)
            return 1
        print(json.dumps(r["theme"], ensure_ascii=False, indent=2))
        for k, v in out["files"].items():
            print(f"  {k}: {v} B")
        return 0
    if c == "validate":
        folder = preset(a.folder) if a.folder in FACTORY and not pathlib.Path(a.folder).is_dir() else pathlib.Path(a.folder)
        r = validate(folder, factory_ok=folder.resolve().parent == PKG)
        if js:
            print(json.dumps({k: v for k, v in r.items() if k != "files"} | {"files": sorted(r["files"])}, ensure_ascii=False))
        else:
            print_report(r)
        return 0 if r["ok"] else 1
    if c == "export":
        r = export(root, a.which, a.dest)
        if js:
            print(json.dumps(r, ensure_ascii=False))
        elif r["ok"]:
            print(f"✓ 已复制到 / copied to {r['path']}: {', '.join(r['files'])}")
        else:
            print({"dest_not_empty": "✗ 目标文件夹不是空的 / the folder is not empty",
                   "unknown_preset": "✗ 只能是 robots、dots 或 active / robots, dots or active"}[r["error"]], file=sys.stderr)
        return 0 if r["ok"] else 1
    if c in ("apply", "reset", "restore"):
        if c == "reset":
            r = apply_preset(root, DEFAULT)
        elif c == "restore":
            r = restore(root, a.entry)
        elif a.target in FACTORY and not pathlib.Path(a.target).is_dir():
            r = apply_preset(root, a.target)
        else:
            r = apply_dir(root, a.target)
        if js:
            print(json.dumps({k: v for k, v in r.items() if k != "report"} |
                             ({"errors": r["report"]["errors"]} if r.get("report") else {}), ensure_ascii=False))
        elif r.get("ok"):
            cur = r["current"]
            if r.get("unchanged"):
                print(f"✓ 已经在用 {cur['id']}，没有变化 / {cur['id']} is already active — nothing changed")
            else:
                print(f"✓ 已换成 {cur['id']} · {_label(cur.get('name'))}；手机几秒内自动刷新 / now {cur['id']} — phones repaint within seconds")
                if r.get("dormant"):
                    print(f"  原来的主题已休眠 / the previous theme sleeps as: {r['dormant']}（agentj theme restore {r['dormant']}）")
            for w in r.get("warnings") or []:
                print(_say(w, "!"))
        elif r.get("report"):
            print_report(r["report"])
        else:
            print({"unknown_entry": "✗ 没有这个休眠主题（看 agentj theme list）/ no such dormant theme (see agentj theme list)",
                   "unknown_preset": "✗ 没有这个出厂主题 / no such factory theme"}.get(r.get("error"), "✗ " + str(r.get("error"))),
                  file=sys.stderr)
        return 0 if r.get("ok") else 1
    return 2


SUBCOMMANDS = ("list", "show", "validate", "apply", "reset", "restore", "export", "schema")
SCHEME = ("light", "dark", "system", "set")     # the older `agentj theme light|dark|system` (appearance.theme) stays as it was


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agentj theme", description=(
        "手机外观主题（颜色、左上角 logo、发送图标、跳舞机器人 / 圆点）：一个文件夹 = theme.json + PNG 图；先 validate，全部通过才能 apply，"
        "原来的自动休眠，随时 restore 或 reset 回出厂 / the phone's look (colours, top-left logo, Send glyph, dancing robots or dots): "
        "a folder = theme.json + PNG images; validate first, only a folder that passes can be applied, the previous one goes dormant, "
        "restore it or reset to the factory look any time"),
        epilog="深浅色照旧 / light or dark as before: agentj theme light | dark | system · 规则 / rules: https://agentj.app/docs/theme/")
    ps = p.add_subparsers(dest="theme_cmd", required=True)
    x = ps.add_parser("list", help="出厂、正在用、休眠的主题 / factory, active and dormant themes")
    x.add_argument("--json", action="store_true")
    x = ps.add_parser("show", help="看一个主题的全部设置（默认正在用的）/ every setting of a theme (default: the active one)")
    x.add_argument("which", nargs="?", default="", help="active（默认）、robots、dots 或主题文件夹 / active (default), robots, dots or a theme folder")
    x.add_argument("--json", action="store_true")
    x = ps.add_parser("validate", help="检查一个主题文件夹（schema + 尺寸、帧数、格式、对比度）/ check a theme folder")
    x.add_argument("folder", help="主题文件夹（theme.json 所在的文件夹）/ the theme folder (where theme.json is)")
    x.add_argument("--json", action="store_true")
    x = ps.add_parser("apply", help="换上一个主题（先检查；原来的自动休眠）/ switch (checked first; the old one goes dormant)")
    x.add_argument("target", help="主题文件夹，或 robots、dots / a theme folder, or robots or dots")
    x.add_argument("--json", action="store_true")
    x = ps.add_parser("reset", help="一键回出厂（跳舞机器人）/ back to the factory look (dancing robots)")
    x.add_argument("--json", action="store_true")
    x = ps.add_parser("restore", help="唤醒一个休眠的主题 / bring a dormant theme back")
    x.add_argument("entry", help="agentj theme list 里休眠主题的名字 / a dormant name from agentj theme list")
    x.add_argument("--json", action="store_true")
    x = ps.add_parser("export", help="把出厂或正在用的主题复制到新文件夹当模板 / copy a preset or the active theme to edit")
    x.add_argument("which", help="robots · dots · active")
    x.add_argument("dest", help="新的空文件夹 / a new, empty folder")
    x.add_argument("--json", action="store_true")
    x = ps.add_parser("schema", help="打印主题 JSON Schema / print the theme JSON Schema")
    x.add_argument("--path", action="store_true", help="只打印文件路径 / only its path")
    return p


def command(args: list) -> int:
    """`agentj theme …` (cli.py hands every `theme` call here before argparse)."""
    if args and args[0] in SCHEME:
        from . import personalize
        return personalize.command(["theme", *args])
    return cmd_theme(parser().parse_args(args))
