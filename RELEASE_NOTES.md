# Agent J 0.24.0a1 release notes

Host 0.24.0a1 / install 0.26.0; phone-web scope.
Phone parity must pass --strict. Full Android/Telegram parity is incomplete.
Full Android/Telegram parity is incomplete.
No APK is included in this release. Android source is for review, not a released app.

Publication status: local preparation only. Live site verification is DEFERRED until
the public commit and tag are pushed, the site is deployed, and cold installation passes.

On failure restore v0.23.2a1 host/site/phone and the captured Dashboard Worker version. Release order: C (Dashboard Worker only, no D1 migration), then A signed host/site/downloads then B phone web immediately after. Normal publication windows remain C 05:00–07:00 JST and A/B 23:00–08:00 JST; outside-window publication requires recorded owner authorization. No relay change. A preserves the already-published P381–P383 website content, checked against the P383 publication receipt and published baseline. Notebook records, card colours and Automation, large-screen controls, onboarding and Telegram recovery ship together. Native Windows, LG TV and physical iOS acceptance are not claimed.
## Changes

- Browse, copy, edit and confirm deletion of notebook records, and choose your own card colours.
- Enable the optional read-only Automation card to inspect schedules and ask your Agent about a job.
- Use direction keys, double-confirm full-screen reading and page scrolling on large screens.
- Open installation help in a new account-page tab; copy system commands and Telegram pairing codes directly.
- Follow real first-time voice preparation stages, completion notices and retry guidance.
- Hold Search to refresh while preserving a draft; email headers and footers follow system appearance.
- Reload service keys after saving or rebinding Telegram; verify your identity to restart through Emergency repair.
- Confirm the working folder before installation; OpenCode setup help uses its offline catalogue and startup guidance.
- 记事本按记录浏览、复制、编辑和确认删除，卡片支持自己的颜色。
- 新增默认关闭的只读自动化卡片，可查看计划并询问 Agent。
- 大屏遥控器支持方向键、双击确认全屏阅读和翻页。
- 安装指南从账号页新标签打开，系统指令与 Telegram 配对码可直接复制。
- 首次语音准备显示实际阶段、完成提示与失败重试。
- 长按搜索可保留草稿刷新，邮件顶栏和页脚跟随系统深浅色。
- 保存密钥和重绑 Telegram 时重新读取服务环境；紧急修复可验证身份后重启服务。
- 安装前确认工作目录，OpenCode 安装说明使用离线目录并提供启动等待提示。

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
