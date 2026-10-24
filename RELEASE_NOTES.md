# Agent J 0.25.7a1 release notes

Host 0.25.7a1 / install 0.27.7; phone-web scope.
Phone parity must pass --strict. Full Android/Telegram parity is incomplete.
Full Android/Telegram parity is incomplete.
No APK is included in this release. Android source is for review, not a released app.

Publication status: local preparation only. Live site verification is DEFERRED until
the public commit and tag are pushed, the site is deployed, and cold installation passes.

Release order: C applies app D1 0037_action_uv.sql before Dashboard; then A signed host/site/downloads, immediately followed by B phone web. No relay or feedback D1 change. Keep0036 revocation state and local security journals permanently. Never replay0037, restore an older database snapshot or use revocation-incompatible code; rollback compatible code only, otherwise repair forward. Native Mac M1 and physical iOS band/installation acceptance remain pending. Local preparation only; no push or deployment authorized.
## Changes

- Use isolated assistants for WeChat and external channels, with reports to the owner and native image input.
- Confirm each sensitive account action with a passkey; review again if its quote or target changes.
- Require this remote’s approval passkey for every outgoing email, mail-setting change, admin card and secret card.
- Use Agent J - <name> consistently while preserving the owner’s chosen name.
- Style friend reports with light cards and green actions; an unknown send result requires the owner to check before resending.
- Complete iOS home-screen installation metadata. Remove and add older shortcuts again; the reported band still needs physical-device verification.
- Improve macOS file access and high-load handling. Native Mac M1 acceptance is pending.
- 微信及外部通道使用独立分身，给主人直报并支持原生识图。
- 账号页敏感操作每次用通行密钥确认，报价和目标变化后重新核对。
- 每次发邮件及修改邮件设置均需本机遥控器通行密钥确认；管理员和密钥卡同样需要确认。
- 商用称呼统一为 Agent J - <名字>，保留主人自取名字。
- 好友直报使用浅色卡片和绿色按钮，发送结果未知时由主人核对后确认重发。
- 补齐 iOS 主屏幕安装元数据；旧快捷方式需删除后重新添加，浅带仍待实体复验。
- 改善 macOS 文件访问和高负载保底；Mac M1 原生验收待实体验收。

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
