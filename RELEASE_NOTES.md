# Agent J 0.20.0a1 release notes

Host 0.20.0a1 / install 0.22.0; phone-web scope.
Phone parity must pass --strict. Full Android/Telegram parity is incomplete.
Full Android/Telegram parity is incomplete.
No APK is included in this release. Android source is for review, not a released app.

Publication status: local preparation only. Live site verification is DEFERRED until
the public commit and tag are pushed, the site is deployed, and cold installation passes.

On publication failure, restore v0.18.4a1 host/site/phone, the captured Dashboard and relay Worker versions (D1 0025 is additive and stays). C Dashboard (app migration 0025_device_names, seat certificate endpoint, account pairing fixes) first, then the relay Worker (seat check present, enforcement off), A front Worker/site/signed downloads/install guide, B phone web.
## Changes

- /goal goal mode: one signed opening card covers the dangerous actions a goal needs by kind and limit; undoable steps outside it get a 10 s countdown card, irreversible / suspicious ones wait for the owner; the heartbeat and new sessions continue a running goal.
- Platform heartbeat on every harness (default 60 min, silent unless something needs the owner); `agentj busy capacity` decides how many background jobs to start.
- E-mail channel (allowlist + sender authentication before the model, reply in the same thread), address book, contact cards and 「转发到邮箱」; any file up to 200 MB; long recordings in 20-minute parts with the screen kept awake; meeting notes.
- One full-screen Settings page (the ≡ menu folded in) with 遥控器管理: list, rename, unbind and add remotes (up to 10, signed controls); a pairing card on every paired remote; terminal-only pairing on an account-bound computer; owner / device names for remotes.
- Phone themes (colours, logo, Send icon, pixel robots or dots) with validate → apply; a menu design system; background robots with per-task cards for Claude Code, Codex and OpenCode sub-agents.
- Fewer false approvals: heredoc bodies are data, Jev may clear what Agent J cannot read (owner threshold), sub-agent cards name their origin; Codex error classes; independent sessions by default; seat enforcement (an unbound host cannot pair).
- Main-Agent core v11 (chairman philosophy, compressed rules); /switch-harness with a guarded switch-back.
- 中文：/goal 目标模式；心跳；邮件通道、通讯录与「转发到邮箱」；任意文件与长录音；设置合成全屏页并能在手机上管理遥控器；主题换肤；误报更少；默认独立会话；席位强制；身份 v11。

- Physical iPhone/Android lock-screen recording, iPhone file sharing, real mailbox providers beyond the tested ones and the relay seat-certificate enforcement (left off) remain pending; local browsers and fixtures do not prove them.
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
