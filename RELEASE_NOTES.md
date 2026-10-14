# Agent J 0.21.0a1 release notes

Host 0.21.0a1 / install 0.23.0; phone-web scope.
Phone parity must pass --strict. Full Android/Telegram parity is incomplete.
Full Android/Telegram parity is incomplete.
No APK is included in this release. Android source is for review, not a released app.

Publication status: local preparation only. Live site verification is DEFERRED until
the public commit and tag are pushed, the site is deployed, and cold installation passes.

On failure restore v0.20.3a1. C (pending app migration 0026 and account pages) then A signed host/site/downloads then B phone web. No relay change. Daily upgrade digest only at 09:00 JST; at most one mail per account per day.
## Changes

- Five menu pages organize chat, workflows, skills, memory and the current Agent.
- Workflow run records retain status and report links.
- Phone login rescue handles expired logins, with a reminder to enable the official setting before Codex device-code login.
- Queued controls show why they are waiting and can be cancelled.
- Quota pacing reminders and feedback addressed to Agent J Father.
- Improved footer, shortcuts and silent coordinated upgrades. Input cards retain their narrower width.
- Fixed approvals --verify false failures on older records.
- The load indicator description now identifies a light segment moving along the line.
- 五页菜单整理聊天、工作流、技能、记忆和当前 Agent。
- 工作流运行账本保留状态与报告。
- 登录过期可从手机启动登录救援，Codex 设备码登录前会提醒开启官方设置。
- 待执行控制命令显示等待原因并可撤销。
- 额度管家提醒消耗节奏；反馈统一称为「Agent J 爸爸」。
- 底栏、快捷键和静默跟随升级改进，聊天输入卡保持原有较窄宽度。
- 修复 approvals --verify 对旧审批记录的误报。
- 负载线说明更正为一段亮色（浅色）在线上来回滑。

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
