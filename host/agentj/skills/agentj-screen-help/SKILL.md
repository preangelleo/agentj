---
name: agentj-screen-help
description: Browser automation is stuck on a QR-code sign-in, a captcha, a verification code or a "are you human" check — push the code or the whole screen to the owner's phone and wait for the scan or the answer. Use for 「卡在扫码」「要扫码登录」「验证码是什么」「人机验证」「把二维码发我手机」「截个屏给我看」, "stuck on a QR code", "captcha", "send the QR to my phone", "ask me what the code is", and whenever a page needs a human before automation can go on.
---

The owner is not at the computer; their phone is. Agent J puts a picture on their paired remotes (end-to-end encrypted,
never chat history, gone when the card ends) and hands their answer back to you. Never try to solve a captcha yourself,
never ask for a password this way (passwords: `agentj secret request`), never push the same card twice in a row.

## 1. A QR code on the page → `agentj qr push`
```
agentj qr push --text "公众号后台登录要扫码" --wait-until page-change --wait-s 170
```
- No picture argument = the current page of Agent J's browser (`--tab <part of the address>` picks the tab,
  `--selector '<css>'` captures only that element). A file works too: `agentj qr push shot.png --text …`.
- The QR code is cut out locally and checked by decoding it again; if that fails the whole picture goes (`crop.method`
  is `local`, `vision` or absent). `--vision` lets the owner's own vision model (AGENTJ_VISION_URL / _MODEL / _API_KEY in
  the environment) locate a code the local detector missed.
- `--wait-until page-change` watches the tab and closes the card by itself when the address changes or the
  `--selector` element disappears → `result: page_changed` = the scan most likely worked. `answered` waits for the owner's
  「我扫好了」 (`result: done`); `none` (default) returns at once with the card `id`.
- Then check the page yourself (signed in? next step visible?) before you carry on. Codes expire (often 1–2 min): on
  `expired`, refresh the page and push once more; after a second miss, stop and tell the owner.

## 2. Anything else, or the code could not be found → `agentj screen ask`
```
agentj screen ask --question "验证码是什么？" --wait-s 170          # the browser page
agentj screen ask --screen --question "请帮我点一下允许" --reply done   # one monitor of this computer
```
The owner sees the screenshot in high resolution (tap = full screen), can scan, tap 「已完成」, or type a short answer.
`result: answered` → the text is in `answer`: type it into this one field only, never store or repeat it. `done` → look at
the page again. `cancelled` → stop and tell the owner where it is stuck. Prefer the page: `--screen` only for things outside
the browser; it takes the focused monitor, needs a desktop session (macOS screencapture, Wayland grim, X11 import / scrot)
and shows only the lock screen when locked; `no_screen` → use the page or a file instead.

## Waiting and results
One call blocks at most 170 s (give the shell a ≥ 3-minute timeout). `pending` → `agentj screen wait <id>` keeps waiting;
the card lives on the phone until its time runs out (QR 2 min, screen 5 min by default, `--ttl` up to 10 / 15 min).
`agentj screen cancel <id>` withdraws it. JSON on stdout; only `shown: true` means it reached the phone. `no_device` /
`unavailable` → nothing was sent: tell the owner in text. Results never contain the code's content.

## The owner's own scripts (no Agent)
A cron / systemd script of the owner can run the same commands with `--from <name>` (shown on the card as
「来自你的自动化：<name>」). Pictures only (PNG / JPEG / WebP ≤ 1.4 MB), a few cards a minute, same user on this computer only.
Screenshots may show private things — send only the page that needs the owner.
