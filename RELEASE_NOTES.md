# Agent J 0.16.3a1 release notes

Host 0.16.3a1 / install 0.20.3; phone-web scope.
Phone parity must pass --strict. Full Android/Telegram parity is incomplete.
Full Android/Telegram parity is incomplete.
No APK is included in this release. Android source is for review, not a released app.

Publication status: local preparation only. Live site verification is DEFERRED until
the public commit and tag are pushed, the site is deployed, and cold installation passes.

On publication failure, restore v0.16.2a1 and its published main, including site and phone web.
## Changes

- Account-page passkey approval adds remotes to another online bound host, approves exact pending devices, resumes a stopped host and enables a specific scheduled task.
- Bound hosts may use account approval instead of a local passphrase; verified sessions last ten minutes. Signed single-use requests bind account, seat and host, expire within five minutes and obey the default-on host veto.
- Pairing material is encrypted to the account browser. The signed control plane is trusted to grant access; its compromise can grant device access. The backend does not store plaintext pairing secrets.
- Main Agent identity core version 7 proposes reusable skills, CLI tools or Workflow Design Bible workflows before creating them with owner consent.
- Friends default to twelve rounds of daily collaboration and technical conversation. Owner cards can delegate a reply or move a friend to Colleagues; quotes, commitments and protected information keep their owner gates.
- Already-installed hosts accept single-use AJI installation codes for account binding; configuration is preserved.
- Four pinned uv platform archives, managed-Python and package-index fallbacks support GitHub-blocked installation. OpenCode requires macOS 13 or newer; Intel Mac cryptography remains below 49.
- Native SOCKS5 and HTTP proxies work through python-socks; relay diagnostics show only proxy scheme and host. Upgrade checks distinguish identity refresh and existing workflow findings from installation failure.
- Additive app migration 0023 and Dashboard C must precede site A and phone B. Every installation artifact receives HEAD and SHA verification under the existing A rollback boundary.

- Evidence includes two local real hosts/Worker/Noise, an isolated QA installation and real-model phone flow, Linux GitHub-blocked installation and four-platform bootstrap simulation.
- Physical macOS installation and launchd death recovery, Android and iPhone lock-screen acceptance remain pending; simulation is not physical-device acceptance.

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
