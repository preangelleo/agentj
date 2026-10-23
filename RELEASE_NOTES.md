# Agent J 0.25.5a1 release notes

Host 0.25.5a1 / install 0.27.5; phone-web scope.
Phone parity must pass --strict. Full Android/Telegram parity is incomplete.
Full Android/Telegram parity is incomplete.
No APK is included in this release. Android source is for review, not a released app.

Publication status: local preparation only. Live site verification is DEFERRED until
the public commit and tag are pushed, the site is deployed, and cold installation passes.

Release order: C (additive app D1 migration 0036, then the Dashboard Worker; feedback database unchanged), then A signed host/site/downloads then B phone web immediately after. No relay change. Keep credential_revocations, host_credential_revocations, host_credential_epochs, epoch/ACK/seq and local credential_revocations.json/security_rotation.json permanently. Do not restore an older database snapshot or downgrade to a host/control plane that ignores revocations; repair forward. The 0.25.4 baseline is not a compatible host/control rollback. B-only phone rollback keeps the compatible host/control plane. Normal publication windows and recorded owner overrides still apply. Local preparation only; publication needs separate authorization. Physical iPhone/Android and native Mac Air/launchd acceptance are not claimed.
## Changes

- Create and verify new access before revoking exposed passkeys or old remotes. An offline computer remains pending until it reconnects and acknowledges.
- Keep the latest phone layout diagnostic on the host and inspect it with agentj diag layout --last. It excludes chat text, URLs and tickets.
- AI sign-in rescue cards show the official authorization link or device code, keep Settings open and recheck the current sign-in state.
- Pre-install upgrade failures clearly retain the original version. Rollback results check whether the old program runs while preserving separate service and health diagnostics.
- Locate a shared macOS Herdr conversation after compaction through its live pane identity. Unknown, conflicting or blocked targets remain protected.
- Hosts remain available with clocks 30 seconds fast or slow. Revocation sync binds each signed request to a monotonic deadline and rejects timeouts and replayed responses.
- Supported provider setup reuses the existing secret-card path. Startup failures provide fixed diagnostics and Ark, Bailian and Kimi options.
- Security records suppress old-package force-install recovery commands. A refused security downgrade cannot be reported as a successful rollback.
- Retain the already-live iPhone visible-water colour fix and diagnostic action. The original Mac Air pale band still needs a sample captured during the fault.
- 先创建并验证新入口，再吊销泄露的通行密钥或旧遥控器；离线电脑回连确认前显示待处理。
- 手机画面诊断在主机保留最近一份，可用 agentj diag layout --last 只读查看；不含聊天正文、网址或票据。
- AI 登录救援卡显示官方授权链接或设备码，留在设置页完成操作，并重新检测当前登录状态。
- 升级在安装前失败时明确仍是原版本；回滚结果以旧程序能否运行为据，同时保留独立的服务和健康诊断。
- macOS 共享 Herdr 会话在压缩后按仍存活的面板身份定位；未知、冲突或阻塞目标保持拒绝操作。
- 电脑时间快或慢30秒也能安全上线；吊销同步使用本次签名请求和单调时钟截止，超时及旧响应仍拒绝。
- 服务商初始配置复用已有密钥卡入口；首次启动失败提供固定诊断及方舟、百炼、Kimi 选项。
- 安全记录存在时不再生成旧包强制恢复命令，也不会把禁止降级误报成回滚成功。
- 保留已上线的 iPhone 可见水体取色与画面诊断入口；原 Mac Air 浅色带原因仍待故障当刻诊断。

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
