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
