# Agent J 0.25.8a1 release notes

Host 0.25.8a1 / install 0.27.8; phone-web scope.
Phone parity must pass --strict. Full Android/Telegram parity is incomplete.
Full Android/Telegram parity is incomplete.
No APK is included in this release. Android source is for review, not a released app.

Publication status: local preparation only. Live site verification is DEFERRED until
the public commit and tag are pushed, the site is deployed, and cold installation passes.

Release order: C Dashboard support knowledge only, no D1 migration; then A signed host/site/downloads, immediately followed by B phone web. No relay change. Preserve0036 revocation state,0037 action UV data and local security journals permanently. Never replay migrations or restore an older database snapshot. Use compatible rollback only, otherwise repair forward. Native Mac M1 and physical iOS acceptance remain separate. Local preparation only; no push or deployment authorized.
## Changes

- Show program-verified AI tool, model, account and elapsed time in ongoing work, with unknown values stated explicitly.
- Confirm each calendar write with a passkey bound to the exact calendar, destination and content.
- Verify saved app passwords before asking again; repair and retry failed phone email sends.
- Queue compaction in the current conversation after writing the handoff when the owner requests it.
- Use route-specific Kimi K3.1 Pro identifiers for membership, platform and OpenCode Go.
- Treat unverified input as non-owner and retain authenticated phone provenance throughout the turn.
- Fix Telegram avatars and duplicate name prefixes; only zero-write doctor checks avoid extra verification.
- 在办事项由程序显示 AI 工具、模型、账号与运行时长；无法核实时明确显示未知。
- 日历修改逐次通行密钥确认，批准绑定具体日历、目的端与内容。
- 邮箱登录失败时先验证已登记授权码；手机发信失败可修复并重试。
- 主人要求写好交接后自行压缩时，可在当前对话排队压缩。
- 区分 Kimi K3.1 Pro 的会员、开放平台与 OpenCode Go 模型标识。
- 来源不明的输入一律不当作主人；已验证手机输入在整轮中保持来源绑定。
- 修复 Telegram 头像与名称前缀重复；只有零写入的 doctor 检查免额外验证。

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
