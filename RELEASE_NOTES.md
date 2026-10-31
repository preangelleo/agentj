# Agent J 0.26.3a1 release notes

Host 0.26.3a1 / install 0.28.3; phone-web scope.
Phone parity must pass --strict. Full Android/Telegram parity is incomplete.
Full Android/Telegram parity is incomplete.
No APK is included in this release. Android source is for review, not a released app.

Publication status: local preparation only. Live site verification is DEFERRED until
the public commit and tag are pushed, the site is deployed, and cold installation passes.

Scope: P590 same-owner admission visibility and heartbeat signals; P591 Linux user service bus isolation, protected service configuration and supported diagnostics. Release order: C Dashboard generated support knowledge (no new D1 migration), then A signed host/site/downloads and front Worker, immediately followed by B phone web. No relay change. On failure restore compatible v0.26.2a1 host/site/phone assets and the captured Dashboard Worker version; otherwise repair forward. Preserve0036 revocation state,0037 action UV data,0038 checkout pending records,0041 seat transfer records and local security journals permanently. Never replay migrations or restore an older database snapshot. Qualification requires the two frozen candidates, freshly derived affected host and version-reader suites, one source-bound full strict parity run, and exact failed-item continuation. Full-host, QA-J, physical Mac and customer-machine acceptance remain unperformed. Local preparation only; actual public export, secret scans, root public Jev and publication receipts remain required. Synchronize Olares receivers after A under the existing operator authorization.
## Changes

- [all] Same-owner tasks show admission and waiting status. Heartbeats show unfinished work so you can check whether a task reached the main Agent.
- [all] Linux isolation blocks access to the user service bus and changes to service configuration. Diagnostics report results only for supported checks.
- [all] 同主人任务显示接收与等待状态，心跳显示未完成事项，方便核对任务是否进入主 Agent。
- [all] Linux 隔离区阻止访问用户服务总线与修改服务配置；诊断按实际支持的检查报告结果。

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
