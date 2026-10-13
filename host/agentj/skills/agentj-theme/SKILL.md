---
name: agentj-theme
description: Change how the owner's phone page looks — 「换个主题」「把绿色背景换成…」「换个颜色」「重新画一个 logo」「换掉发送按钮的火箭」「重新画跳舞机器人」「我想要原来的圆点」「恢复默认外观」「换回上一个主题」, "change the theme", "new colours", "redraw the dancing robots", "new logo / send icon", "go back to the dots", "reset the look". Builds a theme folder (theme.json + PNG files) by the rules, runs `agentj theme validate` until it passes, then `agentj theme apply`; the phone repaints by itself within seconds and the old theme sleeps, so it can always come back.
---

A theme is one folder: `theme.json` + PNG files next to it. Schema: `agentj theme schema` (JSON Schema 2020-12, also
https://agentj.app/schema/agentj-theme-1.json); full rules and examples: https://agentj.app/docs/theme/. Every section is
optional: what the theme leaves out keeps the factory look.

## The loop (always this order)
0. When the owner has not said what to change yet (the phone's 「换个样子」 button or a long press on the robots sends
   「我想定制 Agent J 手机页的外观……请先问我想怎么改」), ask first in one short message — robots or dots? which state
   colours? a new logo or Send icon? — offer two or three concrete ideas, and wait for the answer before building.
1. `agentj theme list` — what is active, the two factory themes (robots = default, dots), the dormant ones.
2. Start from a copy, never from nothing: `agentj theme export robots ~/agentj-themes/<short-name>` (or `dots`, or `active`
   for the current one). Change `id` (lower-case letters, digits, -; not robots / dots) and `name` (zh ≤ 12, en ≤ 24).
3. Make the changes the owner asked for — only those.
4. `agentj theme validate ~/agentj-themes/<short-name>` → fix every ✗ line → validate again, until 「全部通过」.
5. `agentj theme apply ~/agentj-themes/<short-name>` → tell the owner it is on (the phone repaints by itself; nothing to
   reload) and how to go back: the previous theme's dormant name is printed (`agentj theme restore <name>`).
Undo: `agentj theme restore <name>` (any dormant one) · factory look: `agentj theme reset` · the dots: `agentj theme apply dots`.
Never edit files under the state folder by hand; never skip validate. Light / dark stays `agentj theme light|dark|system`.

## What can change (theme.json)
| Section | What | Rules |
|---|---|---|
| `colors.state.{idle,working,waiting,question,offline}` | the whole chat screen per state (factory green / blue / orange / purple / grey) | `#rrggbb`; white text (Lotion #faf9f5) on it ≥ 2.5:1 → not too light; the five clearly different; dark mode deepens them by itself |
| `colors.card.{owner,agent,computer,developer,system}` | fill of the message card on top of a page, by who sent it | `#rrggbb`; dark text (Noir #2c2b26) on it ≥ 4.5:1 → light colours |
| `colors.menu.{background,panel,text,accent,line}` | every menu page — Settings and its pages, All commands, Keyboard shortcuts (factory: Sooty page, Noir panels, Lotion text, Milky Waves accent) | `#rrggbb`; text and the notes (text at 72 %) ≥ 4.5:1 on background and panel; accent ≥ 3:1 on background; the security page keeps the factory colours |
| `colors.dialog.background` | every centred dialog — usage numbers, 在办 robots, load line, confirmations, contact cards (factory: Noir `#2c2b26`) | `#rrggbb`; the Lotion text on it ≥ 4.5:1 (a dark colour) |
| `logo` `{image, disc}` | the logo at the top left of the chat page (one image for every state) | PNG, square 128×128 or 256×256, ≤ 64 KiB, still image, not empty; `disc: false` drops the light round disc behind it |
| `send_icon` `{image}` | the glyph on Send (factory: the rocket) | PNG with transparency, 128×128 or 256×256, ≤ 32 KiB; only the shape counts (painted white); four corners clear; 5–70 % covered |
| `busy` `{style: robots, robots: {sheet_12, sheet_18, names}}` | the dancing robots, one per background Agent | see below |
| `busy` `{style: dots, dots: {colors: [{heavy, light}], blink}}` | round dots instead (1–9 colour pairs; each dot blinks heavy ⇄ light) | heavy and light clearly different |

Custom colours do not need to come from any palette. Cannot change (official whatever the theme says): pairing, safety
code, security, offline and removed screens; the 网页版 badge; the menu's shield line; the home-screen icon; fonts; every
agentj.app page.

## Robots (pixel art)
- N robots (1–16), `names` one per robot (zh ≤ 6, en ≤ 12), top to bottom.
- Two sprite sheets of the same robots, one row per robot, **13 frames** across: 12 dance steps (2.4 s loop), then the bow
  (shown when its work is done):
  - `sheet_12`: 12×12 px art (shown 24 px in the list) → **156 × 12·N** px;
  - `sheet_18`: 18×18 px art (shown 18 px in the header) → **234 × 18·N** px.
- Pixel art only: every pixel fully clear or fully opaque (no anti-aliasing, no soft shadow), ≤ 8 colours per robot, the
  background clear, no empty frame, ≤ 32 KiB each. Feet on the bottom rows, the same size in every frame, so it dances
  in place. The factory sheets (from `export robots`) are the best template: change colours, antenna, eyes, badge.
- Draw with any tool that writes PNG. Without one, Python's standard library is enough:
  ```python
  import struct, zlib
  def png(path, w, h, px):            # px(x, y) -> (r, g, b, a), a = 0 or 255
      def chunk(t, b): return struct.pack(">I", len(b)) + t + b + struct.pack(">I", zlib.crc32(t + b))
      raw = b"".join(b"\0" + b"".join(bytes(px(x, y)) for x in range(w)) for y in range(h))
      open(path, "wb").write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
                             + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))
  ```
  Draw each robot once as 12×12 grids (13 frames), then make the 18×18 version by doubling the same 6 columns and 6 rows
  in every frame (keeps the eyes square) — or draw it at 18×18.

## Reading validate
Every failed rule is one line: `✗ <where in theme.json>: <中文> / <English>`; `!` lines are warnings (an unused file is not
copied). `--json` gives the same as `{"code", "path", "zh", "en"}`. Common ones:
| code | Fix |
|---|---|
| `schema.unknown` / `schema.required` / `schema.pattern` | a typo, a missing field, a wrong format (`#rrggbb`, ids `a-z 0-9 -`, file names `name.png`) |
| `color.contrast` | state colour too light (go darker) / card colour too dark (go lighter) / menu text too close to its background or panel, or the accent too faint |
| `color.distinct` | two state colours look alike — move one |
| `png.size` / `robots.size` | exact pixel size (the message says the size expected and found) |
| `robots.edges` / `robots.colours` / `robots.empty` | anti-aliased pixels / too many colours / an empty frame (which robot and frame is in the message) |
| `send.alpha` / `send.corners` / `send.cover` | the icon needs a clear background, clear corners, 5–70 % covered |
| `file.missing` / `file.size` / `png.animated` | file next to theme.json (no links) / too big / only still PNGs |
| `id.reserved` | robots and dots belong to the factory themes |
Apply re-checks a private copy, so a theme that validates is exactly what the phone gets.
