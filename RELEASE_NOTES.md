# Agent J 0.18.2a1 release notes

Host 0.18.2a1 / install 0.21.8; phone-web scope.
Phone parity must pass --strict. Full Android/Telegram parity is incomplete.
Full Android/Telegram parity is incomplete.
No APK is included in this release. Android source is for review, not a released app.

Publication status: local preparation only. Live site verification is DEFERRED until
the public commit and tag are pushed, the site is deployed, and cold installation passes.

On publication failure, restore v0.18.1a1 host/site/phone. A front Worker/site/signed downloads/install guide; B phone web and web Worker. No Dashboard, D1 migration or relay Worker change.
## Changes

- The first-run checklist never blocks the chat: a closable banner and its own page; a closed checklist stays closed until the owner reopens it. Agent J can adopt an existing loopback CDP browser after one owner confirmation.
- Pages without owner input get a page header with page number and new-message cue; the newest page follows new pages, history only flashes.
- Slash commands end in a done state (compaction / new conversation); a busy shared session says the message is queued instead of held.
- The web app reconnects within seconds (5 s backoff cap, resume watchdog) and keeps the unsent draft.
- config set validates in the running host's service environment; release smoke ASR cache moves to a persistent, self-rebuilding path.
- 中文：首装清单不挡聊天、可关且关了不再弹、可接管已有浏览器；无输入页有页码与新消息提示；压缩完成提示；网页断线快速恢复并保留草稿；「电脑正忙，消息已排队」；config set 不再需要手动加载服务环境。

- Physical iPhone/Android/macOS and founder/native Claude/Telegram cutover remain pending; local browser and cloud/service-manager fixtures do not prove them.
- Original P82 key security and 60s recovery evidence is explicitly reused only while the shared issuer source hash is unchanged; current package/Noise/card/model checks use new hash-bound evidence.

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
