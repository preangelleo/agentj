# Agent J 0.26.2a1 release notes

Host 0.26.2a1 / install 0.28.2; phone-web scope.
Phone parity must pass --strict. Full Android/Telegram parity is incomplete.
Full Android/Telegram parity is incomplete.
No APK is included in this release. Android source is for review, not a released app.

Publication status: local preparation only. Live site verification is DEFERRED until
the public commit and tag are pushed, the site is deployed, and cold installation passes.

Release order: C Dashboard support knowledge, then A signed host/site/downloads and front Worker, immediately followed by B phone web. No new D1 migration or relay change. Preserve existing revocation, action approval, checkout and seat-transfer records; never replay migrations. Qualification covers the seven merged candidates, affected host suites and one source-bound full parity run with exact failed-item continuation. No full-host or QA-J acceptance is claimed. Local preparation only; Jev review and publication remain the operator's steps.
## Changes

- [all] Authorize the listed steps of a same-owner task once. Keys travel through a dedicated encrypted channel; steps outside the grant still require approval.
- [all] Heartbeats check local tasks, calendar items and unfinished work before calling a model. Idle checks use no model tokens; measured heartbeat usage appears in the usage panel.
- [all] Files and replies share one reader. Input cards retain the original words and source for WeChat, Telegram, email and same-owner tasks.
- [codex,opencode] Tool switching explains the limits of a fallback handoff excerpt after a timeout. Isolated sessions can query the host result for the same switch.
- [all] Malformed upgrade-cache receipts trigger a fresh download instead of a misleading verification failure. Signature and package identity checks remain mandatory.
- [all] Unclaimed same-owner tasks wait while the main Agent is busy or disconnected. Claimed tasks and unknown outcomes are never replayed automatically.
- [all] 同主人设备可以用一份任务授权单确认具体步骤，密钥通过专用加密通道传递；授权范围外的操作仍需确认。
- [all] 心跳先在本机检查待办、日历和未完成事项；没事就不调模型，用量弹窗里能看心跳消耗。
- [all] 文件与回复用同一款阅读器；输入卡保留微信、Telegram、邮件和同主人任务的原文与来源。
- [codex,opencode] 切换等待超时，说明交接摘录有局限；隔离会话能查同次切换的主机结果。
- [all] 修复升级缓存收据格式异常导致的误报校验失败；损坏缓存时重新下载，完整签名与身份校验机制依然保留。
- [all] 主 Agent 忙了或者断了，同主人没认领的任务就留着等；已经认领的，或者结果还看不出来的，就不会自动重放。

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
