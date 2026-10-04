# Agent J 0.12.2a1 release notes

Host 0.12.2a1 / install 0.12.2a1; phone-web scope.
Phone parity passes --strict --release v0.12.2a1 with only the two documented B4/B5 waivers.
Full Android/Telegram parity is incomplete.
No APK is included in this release. Android source is for review, not a released app.

Publication status: local preparation only. Live site verification is DEFERRED until
the public commit and tag are pushed, the site is deployed, and cold installation passes.

On publication failure, restore v0.12.1a1 / public f458443c, including site and phone web.
## Changes

- In-app QR camera pairing with bundled Apache-2.0 decoder and paste fallback.
- Pairing/chat/settings follow the visual viewport above the keyboard.
- Resolve harness mise/asdf wrappers without executing them; preserve explicit overrides.
- OpenCode startup progress and turn-idle timeouts give actionable notices.
- Doctor lists installations; desktop linger and install feedback version examples corrected.

## Transitional release exclusions (PROMPT-41)

- B4 shared desktop session and desktop user-input mirroring are NOT implemented.
- B5 owner-permission inheritance and one-card high-risk batching are NOT implemented.
- B3 has local OpenCode + DeepSeek evidence; iOS/Android real-device acceptance remains deferred.
- Only shared-desktop-session and shared-owner-permissions are exempted from phone strict for this tag.

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
- `telegram-channel` (telegram; partial): Telegram 双向通道（私聊/群聊、恶意拦截、回传）. Own bot, owner-private text send/final-reply/stop implemented; groups, media, malicious-group gate pending. Never a signed approval channel.
- `tg-media` (telegram; todo): Telegram 附件与语音接收. No exclusion: full Telegram surface gates remain red until implementation and behavioral tests.
- `tg-groups` (telegram; todo): Telegram 群 profile/allowlist. No exclusion: full Telegram surface gates remain red until implementation and behavioral tests.
- `tg-malicious-gate` (telegram; todo): Telegram 群两层恶意过滤. No exclusion: full Telegram surface gates remain red until implementation and behavioral tests.
- `tg-reply-routing` (telegram; partial): Telegram 来源判定与回传. No exclusion: full Telegram surface gates remain red until implementation and behavioral tests.
- `tg-owner-text` (telegram; partial): Telegram 主人私聊文本. No exclusion: full Telegram surface gates remain red until implementation and behavioral tests.
- `shared-desktop-session` (bridge; partial): 手机与电脑接入同一个会话. P40 B4 pending: adapters still start their own conversations. No desktop input mirroring or tested shared/resume binding; do not claim same-session parity.
- `shared-owner-permissions` (bridge; partial): 共享会话沿用机主权限、真高危同类批量审批. P40 B5 pending: independent-session adapters still impose their own permission rules. No tested owner-policy inheritance or one-card high-risk batching; existing fence retained.
