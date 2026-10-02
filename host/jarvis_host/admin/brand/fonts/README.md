# Fonts (self-hosted, Latin only)

| File | Family / weight | Licence |
|---|---|---|
| `ubuntu-400.woff2` `ubuntu-500.woff2` `ubuntu-700.woff2` | Ubuntu Regular / Medium / Bold — body, buttons, labels | Ubuntu Font Licence 1.0 — `UFL-1.0-ubuntu.txt` (© Canonical Ltd.) |
| `lexend-400.woff2` `lexend-700.woff2` | Lexend Regular / Bold — titles | SIL Open Font License 1.1 — `OFL-1.1-lexend.txt` (© The Lexend Project Authors) |

Copied unmodified from the upstream releases (Ubuntu Font Family; Lexend by Google Fonts). Licence texts from
the google/fonts repository. Both licences allow bundling and serving with a website; keep the licence files next to
the fonts (sync.mjs copies them).

Chinese: we do **not** ship CJK web fonts (several MB). `--font-cjk` in `palette.css` falls back to the system:
PingFang SC → Hiragino Sans GB → Microsoft YaHei → Noto Sans CJK SC → Source Han Sans SC → sans-serif.
