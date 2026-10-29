# Agent J 0.25.20a1 release notes

Host 0.25.20a1 / install 0.27.20; phone-web scope.
Phone parity must pass --strict. Full Android/Telegram parity is incomplete.
Full Android/Telegram parity is incomplete.
No APK is included in this release. Android source is for review, not a released app.

Publication status: local preparation only. Live site verification is DEFERRED until
the public commit and tag are pushed, the site is deployed, and cold installation passes.

Release order: C Dashboard support knowledge only, no D1 migration (app 0039-0041 are live since 0.25.13 and are never replayed); then A signed host/site/downloads, immediately followed by B phone web, in the same window. No relay change, no feedback D1 change. Preserve0036 revocation state,0037 action UV data,0038 checkout pending records,0041 seat transfer records and local security journals permanently. Never replay migrations or restore an older database snapshot. Use compatible rollback only, otherwise repair forward. Qualified by the affected suites of the one hotfix ticket under the owner's 10-27 rule (no fresh full parity, full host or real-install acceptance run for this release). Local preparation only; no push or deployment authorized.
## Changes

- While you're away, heartbeats and scheduled tasks are no longer mistaken for you saying no. Before, with the phone set to "Full access", once you stepped away and no remote was online, ordinary steps in heartbeats and scheduled tasks were refused at once and logged as your refusal, even after a restart; now they simply run.
- A step that truly needs your own OK (such as spending money, deleting data or changing Agent J's own code) leaves its card on your phone with a reminder and continues once you approve; the log says no remote was online instead of recording a refusal.
- 主人不在时，心跳和定时任务不再被误当成主人拒绝。以前手机设成「全放行」后，只要你离开、遥控器不在线，心跳和定时任务里的普通操作就会被立刻拒绝，记录里还写成「主人拒绝」，重启也没用；现在这些操作照常执行。
- 真正需要你亲手确认的操作（比如花钱、删除数据、改动 Agent J 自己的代码），会把卡片留在你手机上并提醒你，等你回来批准后再继续；记录里写明「没有遥控器在线」，不会记成你拒绝。

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
