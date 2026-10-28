# Agent J 0.25.19a1 release notes

Host 0.25.19a1 / install 0.27.19; phone-web scope.
Phone parity must pass --strict. Full Android/Telegram parity is incomplete.
Full Android/Telegram parity is incomplete.
No APK is included in this release. Android source is for review, not a released app.

Publication status: local preparation only. Live site verification is DEFERRED until
the public commit and tag are pushed, the site is deployed, and cold installation passes.

Release order: C Dashboard only (support knowledge, the Terminal Commands help wording and the curved left accent bars), no D1 migration (app 0039-0041 are live since 0.25.13 and are never replayed); then A signed host/site/downloads, immediately followed by B phone web, in the same window. No relay change, no feedback D1 change. Preserve0036 revocation state,0037 action UV data,0038 checkout pending records,0041 seat transfer records and local security journals permanently. Never replay migrations or restore an older database snapshot. Use compatible rollback only, otherwise repair forward. Qualified by the affected suites of the four merged tickets under the owner's 10-27 rule (no fresh full parity, full host or real-install acceptance run for this release). Local preparation only; no push or deployment authorized.
## Changes

- Settings are tidier: Terminal Commands sit in one card with icons; six new emergency switches let you change to another AI when the current one is in trouble; the mail allowlist accepts a whole domain (such as @example.com); notification mail from your allowlist is read-only and never answered.
- WeChat now says why a link dropped or was taken over and how to recover, and Codex approval cards and handovers are clearer; background tasks you set up yourself are no longer refused, and anything that needs your OK still comes to you as a card.
- The coloured bar on the left of notices and quotes now curves with the card's rounded corners; the colours stay the same.
- When something breaks, Agent J first checks for a newer version and asks before upgrading; for recurring automations it now recommends your computer's own scheduler (systemd on Linux, launchd on macOS, Task Scheduler on Windows).
- During Touch ID / Face ID the system sheet briefly drops the web page's connection and the result used to be lost; it now arrives after reconnecting. A key card that fails says why, and expired cards fold away.
- When you reported a problem through Agent J, it sometimes could not read the support team's reply; it now reads the reply and passes it on to you.
- Mail between two Agent J owners could be skipped as automatic mail and never arrive; it is now delivered to the other owner's Agent J.
- 设置页整理了一遍：终端指令收进一张带图标的卡片；新增 6 个紧急切换 AI 的开关，当前的 AI 出问题时可以直接换一个；邮件白名单可以填整个域名（比如 @example.com）；白名单里的通知邮件只读，不会回信。
- 微信断开或被别处接管时会说明原因和恢复办法，Codex 的审批卡和交接更清楚；你自己设的后台任务不再被一律拒绝，需要你确认的操作照常发卡片给你。
- 提示和引用左边的彩色竖线改成随卡片圆角一起弯过去的样式，颜色不变。
- Agent J 遇到故障会先检查有没有新版本，有就先问你要不要升级；你要设定时、重复执行的自动化时，它默认用电脑系统自带的定时任务（Linux 的 systemd、macOS 的 launchd、Windows 的任务计划程序）。
- 用 Touch ID / Face ID 验证时，系统弹窗会让网页短暂断线，验证结果因此丢失；现在重新连上后结果照样送到。密钥卡失败时会写明原因，过期的卡会自动收起。
- 你给 Agent J 提的问题，客服回复之后 Agent J 有时读不到回复内容；现在它能正常读到回复，并接着告诉你。
- 两位 Agent J 主人之间互发的邮件，以前可能被当成自动邮件跳过、对方收不到；现在会正常送到对方的 Agent J。

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
