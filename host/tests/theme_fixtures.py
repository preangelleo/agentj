"""P169 test helpers: a tiny PNG writer and a complete custom theme folder built from the factory robots (no Pillow)."""
import json
import pathlib
import struct
import zlib

from agentj import theme


def png(w, h, px, ctype=6, extra_chunks=()):
    """px(x, y) → (r, g, b, a). ctype 6 = RGBA, 2 = RGB (alpha dropped)."""
    def chunk(t, b):
        return struct.pack(">I", len(b)) + t + b + struct.pack(">I", zlib.crc32(t + b))
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        for x in range(w):
            p = px(x, y)
            raw += bytes(p if ctype == 6 else p[:3])
    body = chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, ctype, 0, 0, 0))
    for t, b in extra_chunks:
        body += chunk(t, b)
    return b"\x89PNG\r\n\x1a\n" + body + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b"")


def disc(size, colour=(44, 43, 38, 255), r=0.42):
    c = size / 2
    return png(size, size, lambda x, y: colour if (x + .5 - c) ** 2 + (y + .5 - c) ** 2 <= (size * r) ** 2 else (0, 0, 0, 0))


def arrow(size):
    """An upward triangle (the Send glyph): clear corners, ~30 % covered."""
    def px(x, y):
        top, bottom = size * 0.15, size * 0.85
        if not top <= y <= bottom:
            return (0, 0, 0, 0)
        half = (y - top) / (bottom - top) * size * 0.35
        return (255, 255, 255, 255) if abs(x + .5 - size / 2) <= half else (0, 0, 0, 0)
    return png(size, size, px)


def crop_rows(sheet: bytes, cell: int, rows: list[int]) -> bytes:
    """Robots `rows` of a factory sheet (in that order) → a new sheet."""
    w, _h, data, _a, _an = theme.read_png(sheet)
    return png(w, cell * len(rows), lambda x, y: tuple(data[rows[y // cell] * cell + y % cell][4 * x:4 * x + 4]))


ROWS = [5, 2, 11]
COLOURS = {"state": {"idle": "#2d4f83", "working": "#8a4fb0", "waiting": "#b0452e", "question": "#5a6f2a", "offline": "#6b6b6b"},
           "card": {"owner": "#fbf2c5", "agent": "#ebd8f5", "computer": "#dff3d1", "developer": "#c4f0f0", "system": "#f8daec"}}


def make_theme(folder, *, style="robots", disc_bg=True, **over):
    """A valid custom theme in `folder` (3 robots from the factory rows ROWS, or 4 dot pairs). `over` replaces top-level keys."""
    folder = pathlib.Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "logo.png").write_bytes(disc(128))
    (folder / "send.png").write_bytes(arrow(128))
    doc = {"$schema": "https://agentj.app/schema/agentj-theme-1.json", "schema": 1, "id": "night-owl",
           "name": {"zh": "夜猫子", "en": "Night owl"}, "colors": json.loads(json.dumps(COLOURS)),
           "logo": {"image": "logo.png", "disc": disc_bg}, "send_icon": {"image": "send.png"}}
    if style == "robots":
        f = theme.preset("robots")
        (folder / "r12.png").write_bytes(crop_rows((f / "robots-12.png").read_bytes(), 12, ROWS))
        (folder / "r18.png").write_bytes(crop_rows((f / "robots-18.png").read_bytes(), 18, ROWS))
        doc["busy"] = {"style": "robots", "robots": {"sheet_12": "r12.png", "sheet_18": "r18.png",
                                                     "names": [{"zh": "一号", "en": "One"}, {"zh": "二号", "en": "Two"}, {"zh": "三号", "en": "Three"}]}}
    else:
        doc["busy"] = {"style": "dots", "dots": {"colors": [{"heavy": "#2d4f83", "light": "#c1e1f0"}, {"heavy": "#b0452e", "light": "#fbc8c6"},
                                                            {"heavy": "#5a6f2a", "light": "#dff3d1"}, {"heavy": "#8a4fb0", "light": "#ebd8f5"}], "blink": True}}
    doc.update(over)
    (folder / "theme.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return doc
