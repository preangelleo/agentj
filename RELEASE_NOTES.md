# Agent J 0.25.11a1 release notes

Host 0.25.11a1 / install 0.27.11; phone-web scope.
Phone parity must pass --strict. Full Android/Telegram parity is incomplete.
Full Android/Telegram parity is incomplete.
No APK is included in this release. Android source is for review, not a released app.

Publication status: local preparation only. Live site verification is DEFERRED until
the public commit and tag are pushed, the site is deployed, and cold installation passes.

Release order: C Dashboard support knowledge only, no D1 migration; then A signed host/site/downloads, immediately followed by B phone web. No relay change. Preserve0036 revocation state,0037 action UV data and local security journals permanently. Never replay migrations or restore an older database snapshot. Use compatible rollback only, otherwise repair forward. Native Mac M1 and physical iOS acceptance remain separate. Local preparation only; no push or deployment authorized.
## Changes

- Show same-owner requests as task cards and return their results automatically.
- Open known commands from the refusal list and use stable tool paths in background services.
- Redact contact details and known credential fragments locally before feedback; show replacement counts and ticket IDs, while refusing residual credentials.
- Distinguish sudo authentication failures from command exits and show redacted error details.
- Confirm AI-tool switches from final receipts, wait for results and supply bounded redacted context when handoff files are missing.
- Preserve the initiating remote for email password cards and report blocked or unreachable host connections accurately.
- 同主人设备派来的请求显示为任务卡，并自动回传办理结果。
- 拒绝清单中的已知命令可直接点击；后台服务使用稳定的工具路径。
- 反馈在本机自动隐藏邮箱、电话和已知凭据片段，成功后显示替换数量和工单号；残余凭据仍拒绝上报。
- 提权失败区分密码验证和命令退出，显示脱敏错误摘要。
- 切换 AI 工具只以最终收据确认成功，支持等待结果；缺少交接文件时提供有界脱敏上下文。
- 邮箱授权码卡保留当前遥控器来源，沙箱拒连和主机不可达如实报告。

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

Preserve0038 checkout operation pending records permanently; no migration replay.
