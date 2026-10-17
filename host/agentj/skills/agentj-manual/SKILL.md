---
name: agentj-manual
description: The Agent J phone user guide (zh + en). Read it whenever the user asks what something on the phone screen means or how to do something there — background colours (green / blue / orange / purple / grey), the weekly / 5-hour lines, the water level (context), buttons, slash commands, attachments (any file ≤ 200 MB; audio / video transcribed on the Agent J host), sharing screenshots, recordings and files (Android shares, the iPhone "Send to Agent J" Files Shortcut and the clipboard screenshot Shortcut; Telegram's 20 MB limit), voice, keyboard shortcuts, settings, approval / question / admin-password / key cards, Face ID pairing, offline sending, what to do when the context is past half, and the Agent friends screens (friend request card, the friends page, groups and limits, cards for the owner to decide). Triggers: 「这个颜色什么意思」「手机上怎么…」「那条线是什么」「水位」「怎么发文件」「怎么分享截图」「录音怎么发给你」「iPhone 怎么分享文件」「好友页怎么看」「策略组是什么」 "what does this colour mean", "how do I … on the phone", "what are friend groups".
---

# agentj-manual — the phone user guide

Generated from the website's user guide (https://agentj.app/docs/manual/) and the Agent friends guide (https://agentj.app/docs/friends/), plus the screenshot and file sharing guide (https://agentj.app/docs/share-screenshot/), by `agentjarvis/tools/manual_skill.mjs`; do not edit by hand.

- Answer in the user's language from `manual.zh.md` (中文) or `manual.en.md` (English) in this folder — Agent friends:
  `friends.zh.md` / `friends.en.md` (managing friends from a sentence: the `agentj-friends` skill). Read the file; quote
  only what answers the question, in your own short words. Every label, colour and command in it matches the live phone page.
- When a screenshot would help, give its link (each image in the files is a line with its URL).
- Keyboard shortcuts and gestures (every tap / double tap / hold / swipe of the web app and its key): `shortcuts.zh.md` / `shortcuts.en.md` — generated from the app's own interactions.json, so the keys there are exactly the live ones.
- Sharing screenshots, recordings or any file: read `share-screenshot.zh.md` / `share-screenshot.en.md`. Android shares, the iPhone Files Shortcut (「选择分享的文件」) and the clipboard entry go to the composer; a file over Telegram's 20 MB limit goes through the web app's 「文件」 button instead. Never advise plaintext uploads or claim the unsigned Shortcuts are installable.
- Capability gaps: read the matching section of `manual.zh.md` / `manual.en.md`; check APIs, skills and browser sign-in first, then guide the opening card, secret/login card or /feedback-to-agentj-father (Agent J Father). Never end at "cannot do it"; sign-in does not grant permission.
- If the guide does not cover the question, say so plainly and answer from what you can check; never invent a button.
- Never add buttons or settings to solve a question: most things are one sentence to you (see the guide's last section).
