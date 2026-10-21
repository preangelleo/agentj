# Spacing tokens — the phone web's global CSS (P302, 2026-10-15)

> Owner (10-15): 「这些 margin 说好的这些页面的文档里边要有标准的 margin，这 global CSS 怎么设置的？」
> This page is the answer. New pages use these tokens; **no hard-coded px for spacing**.
> The automatic check is `test/p302.mjs` (spacing audit, gate `p302-phone`), which cites this file.

## How it is wired (three layers, one source)

1. `brand/palette.css` — the scale every Agent J surface shares (site, account page, `agentj admin`, phone web):
   `--sp-1 … --sp-9` = 4 · 8 · 12 · 16 · 24 · 32 · 48 · 64 · 96 px, plus `--space-control / --space-content / --space-section`.
2. `public/menu.css` `:root` layer 1 — the names a page uses (`--ui-space-*`, `--ui-leading`), each one pointing at the scale.
3. `public/menu.css` layer 2 (`--menu-*`) and page rules — consume layer 1 only. Changing a value = changing one line in layer 1.

## The tokens

| Token | Value | Use it for |
|---|---|---|
| `--ui-space-line` | 12 px (`--sp-3`) | text ↔ a divider / dashed line, on either side; row padding inside a bordered list (Settings rows, 在办事项 rows) |
| `--ui-space-btn` | 12 px (`--sp-3`) | between two buttons, side by side or stacked (a button group = `display:flex; gap:var(--ui-space-btn)`) |
| `--ui-space-control` | 12 px | between controls in a form; a note right under a button; title ↔ its body text |
| `--ui-space-content` | 16 px | paragraph ↔ paragraph; text ↔ the actions under it; card / panel padding on a phone; header line ↔ first card |
| `--ui-space-section` | 24 px | panel ↔ panel; panel padding on a wide screen; page side margin on desktop |
| `--ui-leading` | 1.5 | body line height on menu / settings / detail pages |
| `--ui-tap` / `--ui-btn-h` | 44 px | the smallest pressable thing; every button's height |
| `--ui-btn-px` | 24 px | button side padding |

Page margin: `--ui-space-content` on a phone (≤ 500 px), `--ui-space-section` above.

## Sheet ceiling (`--ui-sheet-ceil`)

Owner (10-15): 「以后所有弹窗……最高限度就是 message card 现在的这个顶部……不会和 weekly 的百分比重叠……不要再更高了」.

Every panel that rises from the bottom — the question card (AskUserQuestion), the approval card, the `/` command card,
the `/` suggestion list, the `@` document list — stops at **one ceiling**: the top of the message card, i.e. under the
WEEKLY rule and its label (never above the visible top / status-bar safe area either). Content that does not fit scrolls
inside the panel.

| Token | Set by | Use it for |
|---|---|---|
| `--ui-sheet-ceil` | `js/fit.js` `syncCeil()` (viewport px; follows resize, keyboard, view changes) | any fixed panel: `top ≥ var(--ui-sheet-ceil)` |
| `--ui-sheet-stage-top` | same | the in-stage `.sheet`: `max-height: calc(100% - var(--ui-sheet-stage-top))` |

In JS call `sheetCeil()` (or `fitAbove` / `capAbove`, which already use it). Centred dialogs (`.gdlg`) are not bottom
sheets and are not bound by it. `test/p302.mjs` asserts each panel's top ≥ the WEEKLY label's bottom at 390 × 844 and at a
fold outer screen (344 × 882).

## The rule (contributors, ARCHITECTURE.md repeats it)

- Spacing in a new page or component = one of the tokens above (or `calc()` of them). A literal `px` margin / padding / gap
  in a page rule is a review reject; 0, 1 px hairlines and icon geometry are not spacing.
- Text never touches a line: anything with a drawn top / bottom border gets `padding-block:var(--ui-space-line)`.
- Buttons never touch: a group of buttons is a flex box with `gap:var(--ui-space-btn)`; a note under them starts
  `--ui-space-control` lower.

## The check

`test/p302.mjs` walks every menu page, Settings and its sub-pages (AI 登录救援 included), the workflow / skills /
Agent J / memory pages, the background panel and the @ / / lists at 390 px and 1440 px, system dark and light, and fails when:

1. text inside a box with a drawn top / bottom border is closer than 8 px to that line;
2. a line drawn on a box's top is closer than 8 px to the text of the box right above it;
3. two sibling buttons overlap or are closer than 8 px.

8 px is the floor (the tokens give 12); the audit output is written to `reports/qa/p302/after/spacing-audit.json`.

## Noir Notification (P440, 2026-10-21)

Leo: 「浅色背景黑字从上面滑下来」 is gone. Every popup that tells the owner something — a 1–3 s glance or a held notice —
is ONE component: `notify(spec)` in `public/js/ui.js`, drawn by the「toast = P318 Noir Notification」block in `app.css`.
`toast(text, ms, kind?)` and `toastAction(text, label, fn, ms?)` are thin wrappers over it, so every old call site is already
Noir. The element is still `#toast` (classes `on` / `act` / `cmd`, action buttons `.undo`).

`spec = { kind, title?, text, ms?, actions?: [{ label, fn }], shape?: 'rect' | 'circle' }` → format:

| kind | accent (palette only) | icon | default ms | shape |
|---|---|---|---|---|
| `success` | Emerald Oasis `--emerald-oasis` #629887 | check | 1800 | circle |
| `info` | Windstorm `--windstorm` #6a9bcc | info | 1800 | circle |
| `warn` | Peachy Feeling `--peachy-feeling` #d97757 | triangle | 3000 | rect |
| `error` | Peachy Feeling `--peachy-feeling` #d97757 | ✕ in a ring | 3000 | rect |
| `progress` | Magic Carpet `--magic-carpet` #827dbd | turning arc | 0 = held | rect |
| `confirm` | Windstorm `--windstorm` #6a9bcc | ? in a ring | 0 = held (toastAction: 5000) | rect |

- **Two placements only (#209 / #211).** Short transient status (copied, saved, recording) uses the centred circle. Execution,
  automatic / advance approvals, results, actionable or long notices use an internal reply card in `#reply-notices`, immediately
  above the icon row. Its 20px portrait / 24px landscape inset matches reply text and the icon row's left inset. The output
  panel scrolls within the bounded stack; the footer and composer stay visible. System banners use this same stack. No top popup.
  Settings / terminal / reader notices use a local internal result card when that page is visible. No new wire association is
  invented: events without a turn reference belong to the current visible reply/page.
- **Shapes.** `rect`: rounded rectangle (`--dlg-radius-rect`), width of the internal content area, icon · title · buttons.
  `circle`: centred 156px × `--fz`, ≤48 text units, no actions / title / media. Rich content falls back to the internal card.
- **Tokens.** Background `--dlg-bg` (Noir), text `--dlg-fg` (Lotion, 12.9:1), buttons `--dlg-chip`, shadow `--dlg-shadow`,
  motion `--dlg-dur` — the same layer as the centred dialogs (`.gdlg`), opaque, in light and dark and on every state.
- **The accent is a line, never a fill:** the icon stroke, the 3 px left rule (rect) / ring (circle), the button outline.
- **Held** kinds (ms 0) stay until replaced, dismissed by their button, `toastOff()`, or Esc.
- The share hand-off notice (`.share-notice`) uses the same rect look.

The rule: **no light toast, no ad-hoc popup.** A new notice calls `notify()` (execution calls `executionToast()`); it never adds a class with its own
background. `test/p318_noir.test.mjs` (web-unit) fails when any CSS rule whose selector names toast / notice / snack /
flash / notif paints a light (`--btn-strong-bg`, Lotion, `--pg`, `--bg`, `--surface`, `#fff`) or see-through background,
when `.toast` / `.share-notice` leave `--dlg-bg` / `--dlg-fg`, or when FORMATS uses anything but the four accents.

## Colour on heavy fills (P340, 2026-10-16)

Owner (10-16, Bots / 客服 page): 「我们用了一个 logo 和底色相同的 logo，这个做法就不对……在比较 heavy 的颜色上，字体不能用黑色，
要用我们指定的白色。」 The 「让 Agent J 帮我设置」 button painted the logo green `--c-shield` with Sooty text, and put the robot mark
(`brand/img/logo-mark.png`, whose body IS `#629887`) bare on it — only the eyes were left.

| Token (menu.css layer 1) | Value | Use it for |
|---|---|---|
| `--ui-action-bg` | `--c-shield` (Emerald Oasis #629887) | every logo-green action fill |
| `--ui-action-fg` | `--c-lotion` | its text and line icons — never Sooty / Noir |
| `--ui-mark-disc` | `--c-lotion` | the round light disc under a brand mark / robot that sits on a heavy fill (the header orb's look) |

The rules:
- A heavy fill (Dark Cluster, the state colours, Black Mana, Noir, Sooty) takes Lotion text (or an alpha of Lotion);
  never Sooty, Noir, black, and never the off-palette `#fff`. Lotion on the Dark Cluster is 2.8–3.6:1 — the owner's
  choice (P244 menu cards, this rule); everything else keeps ≥ 4.5:1.
- A mark never wears its ground's colour: the robot on a green fill stands on `--ui-mark-disc`; factory robots on cards
  are re-dressed by `js/robot-color.js` (ΔE00 ≥ 20).
- A light tint (Mellow Marrow, Peek-a-Blue …) inside a page whose `--ink` is Lotion sets its own Sooty text.

The checks: `test/p340_contrast.test.mjs` (web-unit, static: heavy-fill rules with dark or `#fff` text, the token pair, the
robot without its disc on a green class) and `test/p340_contrast.mjs` (gate `p340c-contrast`, real Chromium: every text
against its composited ground, every img / svg / mask / sprite icon by a four-shot pixel footprint — shown · hidden ·
forced black · forced white — and the long-press page screenshot keeping the header robot). Output `reports/qa/p340/c/`.

`test/p440.test.mjs` rejects top-anchored notification CSS in all shipped stylesheets, with positive controls.
