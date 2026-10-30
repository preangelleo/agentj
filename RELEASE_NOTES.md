# Agent J 0.26.1a1 release notes

Host 0.26.1a1 / install 0.28.1; phone-web scope.
Phone parity must pass --strict. Full Android/Telegram parity is incomplete.
Full Android/Telegram parity is incomplete.
No APK is included in this release. Android source is for review, not a released app.

Publication status: local preparation only. Live site verification is DEFERRED until
the public commit and tag are pushed, the site is deployed, and cold installation passes.

Release order: C Dashboard support knowledge and plaza redaction, no new D1 migration; A signed host/site/downloads and front Worker credential scanning, then B phone web. Synchronize Olares receivers after A. Preserve existing D1 revocation, action UV, checkout and seat-transfer records; never replay migrations or restore an older database snapshot. Qualification uses one fresh full parity run and freshly derived affected host suites for all seven merged candidates. No full-host or QA-J acceptance is claimed. Local preparation only; public baseline and ready require actual 0.26.0 publication evidence. No push or deployment authorized.
## Changes

- [All AI coding tools] Move existing scheduled tasks to the host’s system scheduler after confirming on your phone. Review access before moving; move tasks back or move them again after changes.
- [All AI coding tools] Confirmation cards show the Agent’s note; expand long reasons. Choose notification sounds in Settings.
- [OpenCode] With no API key, a supported free model is selected before the first message and the header follows the actual model. Switch back to Codex if none is available.
- [All AI coding tools] The email allowlist consistently says Owner. Release history shows notes that apply to the current host.
- [All AI coding tools] Fixed stale state during tool switching and recovery after failed switches. Access codes are redacted and scheduled tasks execute validated instructions.
- 【所有 AI 编程工具】已有定时任务可以在手机确认后改用主机的系统定时；迁移前说明运行权限，迁移后可退回，任务改了需要重新迁移。
- 【所有 AI 编程工具】确认卡增加「Agent 说明：」，长理由可以展开阅读全文；可在设置里选择提示音。
- 【OpenCode】没有 API Key 时会预选可用免费模型，顶栏与实际模型保持同步；没有可用模型时提供切回 Codex 的入口。
- 【所有 AI 编程工具】邮件白名单统一使用「主人」称谓；历史版本说明只展示当前主机适用的内容。
- 【所有 AI 编程工具】修复切换过程中旧状态回写、失败后状态不一致及重试入口；补齐访问码脱敏，定时任务只执行已核对的指令。

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
