# Agent J 0.25.6a1 release notes

Host 0.25.6a1 / install 0.27.6; phone-web scope.
Phone parity must pass --strict. Full Android/Telegram parity is incomplete.
Full Android/Telegram parity is incomplete.
No APK is included in this release. Android source is for review, not a released app.

Publication status: local preparation only. Live site verification is DEFERRED until
the public commit and tag are pushed, the site is deployed, and cold installation passes.

Release order: C (Dashboard support/knowledge and installation guidance, no D1 migration), then A signed host/site/downloads then B phone web immediately after. No relay change. The0.25.5 baseline is credential-revocation compatible. Keep0036 credential_revocations, host_credential_revocations, host_credential_epochs, epoch/ACK/seq and local credential_revocations.json/security_rotation.json permanently. Never restore an older database snapshot or downgrade to a host/control plane that ignores revocations. Only compatible rollback, otherwise repair forward. Local preparation only; publication needs separate authorization. Physical iPhone/Android and native Mac acceptance are not claimed.
## Changes

- Peer sessions report into the main thread using your local name; explicit replies stay on the original peer channel.
- Resume a failed multi-step workflow with a phone signature; non-idempotent steps still need confirmation.
- Explain dispatch recommendations using fresh verified readings from both accounts; missing data yields no recommendation.
- Check Kimi K3.1 Pro candidates separately for API, Coding and OpenCode Go routes and actual account availability.
- Offline administrator approvals retain the same card and deadline; reconnect resends the request and expiry restores no authority.
- Use official headless APIs for Codex review and OpenCode init/review; label Codex init as terminal-only.
- The main Agent can suggest importing unmanaged keys from fixed registered sources through existing secret cards, showing names only.
- Reuse the microphone during foreground takes; stop capture when hidden or locked. Recording again after returning may require permission.
- 好友分身默认直报主对话，使用你的本机备注；明确回复只走原好友通道。
- 失败的分步工作流可从手机签名续跑，非幂等步骤仍需确认。
- 派活前根据新鲜且可核实的双账号用量给出可解释推荐；缺数据就不推荐。
- Kimi K3.1 Pro 候选按 API、Coding 和 OpenCode Go 路线及账号可用清单核对。
- 离线管理员审批保留原卡和截止时间，重新连上重发原请求；过期不恢复授权。
- Codex审查及OpenCode初始化/审查接入官方无头接口；Codex初始化如实标明终端专用。
- 主 Agent 可建议通过既有密钥卡导入固定来源的未管理密钥，只展示变量名。
- 前台连续录音复用麦克风；锁屏或切走后停止采集，回来再次录音时可能需要授权。

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
