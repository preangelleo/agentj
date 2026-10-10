# Agent J 0.18.0a1 release notes

Host 0.18.0a1 / install 0.21.6; phone-web scope.
Phone parity must pass --strict. Full Android/Telegram parity is incomplete.
Full Android/Telegram parity is incomplete.
No APK is included in this release. Android source is for review, not a released app.

Publication status: local preparation only. Live site verification is DEFERRED until
the public commit and tag are pushed, the site is deployed, and cold installation passes.

On publication failure, restore v0.17.4a1 host/site/phone and the recorded Dashboard baseline. Revalidate C static Dashboard baseline; publish A front Worker/site/signed downloads/install guide, then B phone web and web Worker including /.aj/rt Cookie routes. No new D1 migration or relay backend change.
## Changes

- Long tasks: persistent plan/approval/checkpoints, bounded capabilities and truthful browser/Google evidence; identity COREv9.
- Browser automation, opt-in awake controls, login checks/QR approval and good-morning; first-run checklist and pinned isolated Google CLI. Full Google OAuth remains 0.18.1.
- Shared Claude private instruction files and native controls; Telegram provenance, owner/family/proxy ordering, albums, forward-only cursor and one-poller cutover. Owner-command executables remain private and their output stays outside model/history/logs.
- Repair non-accept writable shared Claude inbound layers with private backups, respecting repository-root local settings and legacy cwd layers. Managed policy is read-only; older sessions require a new session or owner-initiated /clear.
- Retain all P124 sign-in, quota, read-aloud and WebKit fixes; P108 excluded.
- 中文：整合长程任务、浏览器/保活/早安、首装清单与Google工具、共享原生会话及Telegram切换；修可写入站策略并提示新会话生效，managed只读。完整Google OAuth留0.18.1。

- No live founder/Claude/Telegram cutover is claimed. Preserve the Herdr diagnostic route and qualify a new native session before switching the same bot with drain fencing and monotonic cursor import.
- Physical iPhone/Android/macOS acceptance remains pending. Local WebKit/Chromium and cloud/service-manager fixtures do not establish physical or founder cutover readiness.

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
