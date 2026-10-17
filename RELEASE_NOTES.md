# Agent J 0.23.1a1 release notes

Host 0.23.1a1 / install 0.25.1; phone-web scope.
Phone parity must pass --strict. Full Android/Telegram parity is incomplete.
Full Android/Telegram parity is incomplete.
No APK is included in this release. Android source is for review, not a released app.

Publication status: local preparation only. Live site verification is DEFERRED until
the public commit and tag are pushed, the site is deployed, and cold installation passes.

On failure restore v0.23.0a1. Release order: C (Dashboard and pending additive app migrations 0028, 0029, 0030) in 05:00–07:00 JST; then the relay hook domain and v5-hooks migration; web-page-report 1.3.0 before or with host A; A signed host/site/downloads followed immediately by B phone web in the same window.
## Changes

- Cards brings notes, to-dos and your own cards to the logo menu, with card links and @ mentions.
- External services can notify the Agent through Webhooks; your scripts can send messages and pictures to your phone.
- Run common diagnostics from Settings after Face ID; keys are masked before results appear.
- Subscribe to the Daily AI News email in Settings; use credits to query the linked content library.
- Reports share a template and data-validation workflow, with email layouts and send-to-owner support.
- Search across cards, skills and Settings; voice transcripts fill the input card and copy in one tap.
- Models & API keys discovers keys already on the Agent J host; Face ID can copy them into Agent J.
- OpenCode · Volcano Ark presets and quota-aware routing for new background work.
- Read-only searches no longer trigger false approval prompts; folder dumps that include credential files still ask.
- Corrected the splash icon background, robot centering, badges and card row alignment.
- Clearer offline help, a platform recovery guide and keyboard dismissal after sending.
- Cards 卡片：在左上角菜单集中放记事本、待办和你自己的卡片，支持卡片链接与 @ 提及。
- 外部服务可通过 Webhook 通知 Agent；自己的脚本也能把消息和图片发到手机。
- 设置页常用诊断命令可以用 Face ID 确认后直接执行，结果先遮住密钥再显示。
- Daily AI News 每日邮件可在设置中订阅；内容库支持按点数查询宏观阿尔法资料。
- 网页报告有统一模板与邮件版，可以校验数据、选模板、发给自己。
- 全局搜索覆盖卡片、技能和设置；语音转写补回输入卡，原话一键复制。
- 模型和 API Key 页可发现 Agent J 主机已有的 key，经 Face ID 后收进 Agent J。
- 支持 OpenCode · 火山方舟预设；额度接近上限时，新后台任务按配置改派。
- 修复只读查找误弹审批，同时恢复对会连带读出凭据的整目录命令的提醒。
- 修复启动图标白底与机器人偏移、徽标和卡片行对齐。
- 断线提示更清楚，发送后收起软键盘，恢复指南覆盖各平台。

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
