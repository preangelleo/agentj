# Agent J 0.20.3a1 release notes

Host 0.20.3a1 / install 0.22.3; phone-web scope.
Phone parity must pass --strict. Full Android/Telegram parity is incomplete.
Full Android/Telegram parity is incomplete.
No APK is included in this release. Android source is for review, not a released app.

Publication status: local preparation only. Live site verification is DEFERRED until
the public commit and tag are pushed, the site is deployed, and cold installation passes.

On publication failure, restore v0.20.2a1 host/site/phone. A front Worker/site/signed downloads/install guide/install.sh/install.ps1/install assistant; B phone web, right after A in the same window. No relay Worker change. Batch C (its own 05:00–07:00 window) applies app migration 0026 and deploys the Dashboard (account page: one install command, remote pairing password, globe language menu).
## Changes

- One command installs everything: `curl -fsSL https://agentj.app/install.sh | bash -s -- AJI-…` on macOS / Linux, `install.ps1` on Windows (WSL2 + Ubuntu, one administrator prompt); the install code is redeemed once, a failed step says what to do and never removes the service.
- The approval passphrase is now the remote pairing password: terminal pairing needs it, the account page can add a remote with it (sealed to a one-time host key, five tries), or with a passkey; `agentj passphrase offer` asks once during install.
- A new host's first message is never silent: harness first-run settings are pre-written, a retry card shows after the fifth API retry, a stall card quotes what the agent is waiting for, Claude defaults to Opus 5.5 · medium and falls back once on an unsupported-model 400; `agentj doctor` has a first-setup line.
- Phone web: model / effort pill commits after 5 s of quiet; /switch-harness is always in the / list; a refresh is offered only when the page server already serves the newer page (otherwise one quiet header line), never twice for the same version.
- Site: /docs/install/ by computer (Windows / macOS / Linux) × four ways; a round globe language menu on site, account and admin.
- 中文：一条命令安装（install.sh / install.ps1）；遥控器配对密码（终端必需、账户页可用）；新装第一条消息不再无声卡住、Claude 默认 Opus 5.5；模型/强度停手 5 秒提交；「/」里有 /switch-harness；电脑先升级时不再反复弹刷新；安装页三系统×四种方式；地球仪语言菜单。

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
