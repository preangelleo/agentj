# Agent J 0.26.5a1 release notes

Host 0.26.5a1 / install 0.28.5; phone-web scope.
Phone parity must pass --strict. Full Android/Telegram parity is incomplete.
Full Android/Telegram parity is incomplete.
No APK is included in this release. Android source is for review, not a released app.

Publication status: local preparation only. Live site verification is DEFERRED until
the public commit and tag are pushed, the site is deployed, and cold installation passes.

Local unpublished candidate: Olares lifecycle management and isolated AI-tool discovery, bundled OpenCode2.2.5, and app upgrades through Olares Market. Deployment order is Dashboard support knowledge, signed host/installer/site, then the phone web page. This version introduces no D1 migration. Existing0.26.4 download files and the featured/Story website remain available.
## Changes

- [opencode] Bundled OpenCode is now 2.2.5, with separate owner login guidance for native Claude Code and Claude Agent inside OpenCode.
- [all] The Olares app uses its trusted startup layer for AI tool scans and switches, verifies restart results, and restores the previous tool if startup fails.
- [all] App upgrades on Olares now point to Olares Market instead of the ordinary Linux host updater.
- [opencode] 随包 OpenCode 升至 2.2.5，区分原生 Claude Code 与 OpenCode 内 Claude Agent 的本人登录入口。
- [all] Olares 应用通过受信启动层处理 AI 工具扫描和切换，确认重启结果；启动失败时恢复原工具。
- [all] Olares 应用升级明确转到 Olares Market，不再误入普通 Linux 主机升级。

## Material acceptance limits

- Candidate validation is incomplete. Automated mock and synthetic local-account results do not establish real paid-seat acceptance. Native Claude Code and Claude Agent inside OpenCode require separate authentication and model/tool validation.
- Linux amd64 lifecycle coverage, arm64 executable/tool smoke and physical macOS/WSL/phone testing are separate acceptance areas; a result in one area does not establish the others.

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
