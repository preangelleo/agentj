# Agent J 0.26.4a1 release notes

Host 0.26.4a1 / install 0.28.4; phone-web scope.
Phone parity must pass --strict. Full Android/Telegram parity is incomplete.
Full Android/Telegram parity is incomplete.
No APK is included in this release. Android source is for review, not a released app.

Publication status: local preparation only. Live site verification is DEFERRED until
the public commit and tag are pushed, the site is deployed, and cold installation passes.

Scope: live local-image delivery and current-owner-page file show only. Release order: C generated Dashboard support knowledge (no new D1 migration), then A newly signed host/site/downloads and front Worker, immediately followed by B phone web. Use the new 0.26.4a1 artifacts; the earlier 0.26.3a1 INTERNAL wheel is not a published upgrade. Preserve existing D1 records and security journals; never replay migrations. Local release preparation only: publication, installation on the owner's host and service restarts remain separate operator actions.
## Changes

- [all] Use agentj file show in an admitted owner phone turn to attach a local file to the current page; without a live owner turn it fails explicitly.
- [all] Complete local image references can appear while a turn is running, without waiting for tools and the reply to finish.
- [all] Image preparation and failure states are clearer. A queued or attached-to-page receipt does not mean the client downloaded the file or the owner saw it.
- [all] 在当前获准的主人手机回合，用 agentj file show 把本地文件附到当前页；无有效主人回合会明确失败。
- [all] 回复中的完整本地图片引用可在回合进行中显示，继续执行工具时无需等到回复结束。
- [all] 图片准备和失败状态更清楚；排队或附到页面的回执不代表客户端已下载或主人已看到。

## Material acceptance limits

- This fix was tested with isolated Linux subprocesses/owner sockets and a real Chromium page using a synthetic image while the turn remained active. Physical phones, macOS, WSL and end-to-end real Claude Code/Codex/OpenCode model sessions remain untested; Kimi/Qwen ACP file-show admission is not integrated or accepted.
- file show requires a live admitted owner phone turn; it cannot choose a device, group or friend or create an outgoing message from a standalone desktop turn. queued / attached_to_page do not prove client download or owner visibility.
- 中文：本修复已在 Linux 隔离子进程、主人套接字及真实 Chromium 合成图片页面验证，回合未结束即可成图。实体手机、macOS、WSL 和 Claude Code/Codex/OpenCode 真实模型回合端到端未验；Kimi/Qwen ACP 未接入或验收 file show。无有效主人手机回合会失败，不能指定设备、群、好友或另建外发消息；排队或已附到页面不代表客户端下载或主人已看到。

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
