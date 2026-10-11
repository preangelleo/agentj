# Agent J 0.18.4a1 release notes

Host 0.18.4a1 / install 0.21.10; phone-web scope.
Phone parity must pass --strict. Full Android/Telegram parity is incomplete.
Full Android/Telegram parity is incomplete.
No APK is included in this release. Android source is for review, not a released app.

Publication status: local preparation only. Live site verification is DEFERRED until
the public commit and tag are pushed, the site is deployed, and cold installation passes.

On publication failure, restore v0.18.3a1 host/site/phone. A front Worker/site/signed downloads/install guide; B phone web. No Dashboard code, D1 migration or relay Worker change (the P145 Dashboard stylesheet is an optional static redeploy).
## Changes

- Shared Claude follows a dead pinned session to the one live owner session in the folder (never a headless or background one; ambiguous cases ask); `agentj doctor --reconnect` repairs service, relay and Agent in turn; `agentj status --json` adds a hint.
- Main-Agent core v10: Chairman address and hierarchy, CEO / sub-Agent delegation with a hand-off line first, remembered delegation rules in MEMORY.md that never lift dangerous-operation approvals, and the agentj-handoff skill.
- Work-in-progress dots on the reply card (owned stream-json, shared Claude hooks, workflow runs, `agentj busy`), a global centred dialog that also carries the upgrade notice, keyboard ← → ↑ ↓ card navigation; Esc no longer interrupts.
- A shared Claude turn that ends on a tool call (ScheduleWakeup, Monitor) commits from the transcript's turn end with its last text; compaction / clear recover through a host system note asking for the handoff first.
- macOS launchd env loads configured `key_env` names safely; Telegram replies render Markdown as HTML with a plain fallback; shared Claude corrects a missed Stop after continuous quiet idle.
- Busy-session queue card: received + position; first-run welcome explains the hierarchy; agentj.app spacing contract and plaza screens.
- 中文：手机变灰自动接回（不行就 agentj doctor --reconnect）；董事长助理身份 v10 派活给 CEO / 小助理；回复卡彩色圆点显示后台在办；以工具收尾的回合也能把回复送到手机；压缩/清空后先读交接；官网间距统一。

- Physical iPhone/Android/macOS, a real /compact on Claude Code and founder/native Claude/Telegram cutover remain pending; local browser, replayed transcripts and cloud/service-manager fixtures do not prove them.
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
