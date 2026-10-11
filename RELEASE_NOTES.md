# Agent J 0.18.3a1 release notes

Host 0.18.3a1 / install 0.21.9; phone-web scope.
Phone parity must pass --strict. Full Android/Telegram parity is incomplete.
Full Android/Telegram parity is incomplete.
No APK is included in this release. Android source is for review, not a released app.

Publication status: local preparation only. Live site verification is DEFERRED until
the public commit and tag are pushed, the site is deployed, and cold installation passes.

On publication failure, restore v0.18.2a1 host/site/phone and the captured relay Worker version. relay Worker first (pending-slot limit); A front Worker/site/signed downloads/install guide; B phone web. No Dashboard or D1 migration change.
## Changes

- Every approval reaches the phone: shared Claude cards are no longer withdrawn in favour of another approval hook; missing hooks are restored; control-socket resume/config/send/revoke need an owner claim.
- Dangerous-command checks add an `opaque` category (encoded, spliced or shell-piped commands) and flag credential exfiltration; key-name-only greps stay quiet.
- An independent session whose sandbox cannot start refuses to launch unless the owner allows one launch with a passkey; tasks and native bots no longer inherit the host service's secret environment.
- Telegram forwards and group text are external data; group attachments are checked and budgeted before download.
- The relay caps pending (unauthenticated) slots per source; the installer verifies the minisign signature before writing the wheel; the front Worker forwards the bots admin route and caps feedback streams.
- Host disk writes leave the event loop; update apply rolls back on failure and restarts when idle; iPhone lock-screen resume keeps the layout; OpenCode shared replies follow the configured language.
- 中文：审批一定能在手机上处理；危险命令识别更严并新增「看不清要做什么」；沙箱起不来不会悄悄裸跑；定时任务与客服 bot 拿不到主机密钥；群消息与转发更安全；一键安装验签；中继防占槽；iPhone 锁屏回来布局正常；网页更不容易卡。

- Physical iPhone/Android/macOS and founder/native Claude/Telegram cutover remain pending; local browser and cloud/service-manager fixtures do not prove them.
- Account-page pairing still trusts the signed control plane, and the Linux fence mounts the whole filesystem (`--dev-bind / /`) with the session bus bound back; both are documented, not closed.

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
