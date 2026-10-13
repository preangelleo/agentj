# Agent J 0.20.2a1 release notes

Host 0.20.2a1 / install 0.22.2; phone-web scope.
Phone parity must pass --strict. Full Android/Telegram parity is incomplete.
Full Android/Telegram parity is incomplete.
No APK is included in this release. Android source is for review, not a released app.

Publication status: local preparation only. Live site verification is DEFERRED until
the public commit and tag are pushed, the site is deployed, and cold installation passes.

On publication failure, restore v0.20.1a1 host/site/phone. A front Worker/site/signed downloads/install guide/install assistant; B phone web. No relay Worker change and no D1 migration in this publication (the account page in the closed palette and the P195 partner overrides ship later as a Dashboard-only batch C with app migration 0026).
## Changes

- Approval / question / secret cards are rebuilt from a signed snapshot whenever the phone comes back; cards handled elsewhere close, expired ones say so.
- An upgrade first lists what it would interrupt and asks on the phone; Settings has the upgrade method (automatic when idle / ask first) and an upgrade button.
- Owner-bound files (audio in .agentj/outbox, files sent with --to-owner) are no longer held back as possible secrets; `agentj mail send --to-owner`; `agentj mail expect` waits for one mail from a named sender.
- Phone web: no stray composer scrollbar or focus ring, Telegram forwards keep Markdown, a share-as-web-report button, a centred recording card with lock / dim / cancel, the usage dialog, unread jump, contacts manager, read-only list of the computer's own scheduled tasks, one global centred dialog.
- Owner data (friends, card, remote names, contacts) is snapshotted before every upgrade and healed at start; multi-instance on one computer is public (`agentj --instance N`); fresh installs get the chairman core documents.
- 中文：审批卡快照重建；升级前先列出会打断的事并在手机确认；发给主人的文件不再拦；mail --to-owner / mail expect；输入框滚动条与焦点框、转发 TG 富文本、分享成网页报告、录音卡、用量弹窗、未读跳转、通讯录管理、本机定时任务只读列表；升级前快照与自愈；同机多实例公开；新装文档带董事长核心思想。

## Material acceptance limits

- Android arbitrary-name wake recognition, false-accept/false-reject rates, background operation,
  battery behavior and speech-model licensing are not cleared for a released app.
- Paid-provider sound and cost are unverified; no complete voice catalogue or hardware benchmarks.
- Cross-format migration and power-loss recovery remain unproven; no 99% first-boot AI success claim.
- Telegram input can be dropped after an offset failure; output has no durable retry queue.
- iOS background hotword listening is unsupported.

## Known unfinished inventory entries

- `android-file-provider` (android; partial): Android：选中文件先复制到 App 缓存，相机照片直写. Agent J product fork builds/test APK exists; real-device wake/background/lockscreen and operator-JS trust acceptance pending.
- `android-gboard-image` (android; partial): Android：Gboard 剪贴板图片粘贴. Agent J product fork builds/test APK exists; real-device wake/background/lockscreen and operator-JS trust acceptance pending.
- `android-wake-word` (android; partial): Android：「hey jarvis」唤醒词. Agent J product fork builds/test APK exists; real-device wake/background/lockscreen and operator-JS trust acceptance pending.
- `android-native-take` (android; partial): Android：唤醒录音交给页面转写. Agent J product fork builds/test APK exists; real-device wake/background/lockscreen and operator-JS trust acceptance pending.
- `android-listen-orb` (android; partial): Android：唤醒后的聆听圆球. Agent J product fork builds/test APK exists; real-device wake/background/lockscreen and operator-JS trust acceptance pending.
- `android-quick-tile` (android; partial): Android：快捷设置「JARVIS」开关. Agent J product fork builds/test APK exists; real-device wake/background/lockscreen and operator-JS trust acceptance pending.
- `android-boot-listen` (android; partial): Android：开机/升级后恢复监听. Agent J product fork builds/test APK exists; real-device wake/background/lockscreen and operator-JS trust acceptance pending.
- `android-status-settings` (android; partial): Android：监听状态与设置页. Agent J product fork builds/test APK exists; real-device wake/background/lockscreen and operator-JS trust acceptance pending.
- `android-barband` (android; partial): Android：手势条颜色与水位. Agent J product fork builds/test APK exists; real-device wake/background/lockscreen and operator-JS trust acceptance pending.
- `android-reply-notify` (android; partial): Android：新回复锁屏通知. Agent J product fork builds/test APK exists; real-device wake/background/lockscreen and operator-JS trust acceptance pending.
- `android-deep-link` (android; partial): Android：点通知直达那一页. Agent J product fork builds/test APK exists; real-device wake/background/lockscreen and operator-JS trust acceptance pending.
- `android-popup-on-wake` (android; partial): Android：唤醒后悬浮聆听球 / 把 App 拉到前台. Agent J product fork builds/test APK exists; real-device wake/background/lockscreen and operator-JS trust acceptance pending.
