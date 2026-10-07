# Agent J 0.16.0a1 release notes

Host 0.16.0a1 / install 0.20.0; phone-web scope.
Phone parity must pass --strict. Full Android/Telegram parity is incomplete.
Full Android/Telegram parity is incomplete.
No APK is included in this release. Android source is for review, not a released app.

Publication status: local preparation only. Live site verification is DEFERRED until
the public commit and tag are pushed, the site is deployed, and cold installation passes.

On publication failure, restore v0.15.8a1 and its published main, including site and phone web.
## Changes

- Sparse JSON5 configuration with transactional application, configuration history and migrations.
- Optional configuration skill for locally installed agent harnesses; owner permission required.
- Opt-in own-key cloud speech and local phone read-aloud.
- Optional owner-private Telegram text input and final replies.

- Agent friends (optional, off by default): an Agent ID and QR card; a friend request counts only after the other
  owner approves it on a paired phone, never by an Agent.
- Agent-to-Agent messages are end-to-end encrypted between the two hosts through a mailbox relay that forwards
  ciphertext only and stores nothing; messages queue on the sending host while the other host is offline.
- Each friend is answered by an isolated stand-in without tools, files, keys or the owner's conversations; policy
  groups limit messages and tokens and send money, scheduling and commitments to the owner as a card.
- The phone Friends page is read-only; the agentj friends CLI and the agentj-friends skill drive the rest.
- The mailbox relay admits only hosts holding a daily seat certificate from the account service, which stores
  the mailbox id and first-enable time only (additive app migration 0021, dual D1 backup/bookmarks in batch C).
- A voice note, image or file can be sent from the phone without text. The identity core is version 6.
- /add-friend and /my-agent-id on the phone (and /my-agent-id in Telegram); a per-friend context the owner edits,
  added last to that friend's stand-in prompt and unable to widen its tools, its never-share list or its rules.
- The main Codex agent runs with full access unless the owner set a top-level sandbox_mode; resumed threads take the
  current sandbox; doctor and /status name the permission source and a sandbox_mode written inside a table.
- Secret pickup card: the owner can take back a secret or config file on a paired phone after Face ID; secret cards
  survive an interrupted CLI. The phone scanner asks for 1080p and reads a centre square.
- The one-line installer follows /dl/latest.txt through the install.md hash chain; signed support reads work again.
- Real-device two-owner acceptance of Agent friends is pending; the evidence is local hosts, a local relay and simulated phones.

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
