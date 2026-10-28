# Agent J 0.25.18a1 release notes

Host 0.25.18a1 / install 0.27.18; phone-web scope.
Phone parity must pass --strict. Full Android/Telegram parity is incomplete.
Full Android/Telegram parity is incomplete.
No APK is included in this release. Android source is for review, not a released app.

Publication status: local preparation only. Live site verification is DEFERRED until
the public commit and tag are pushed, the site is deployed, and cold installation passes.

Release order: C Dashboard support knowledge only, no D1 migration (app 0039-0041 are live since 0.25.13 and are never replayed); then A signed host/site/downloads, immediately followed by B phone web, in the same window. No relay change, no feedback D1 change. Preserve0036 revocation state,0037 action UV data,0038 checkout pending records,0041 seat transfer records and local security journals permanently. Never replay migrations or restore an older database snapshot. Use compatible rollback only, otherwise repair forward. Qualified by the affected suites of the one hotfix ticket under the owner's 10-27 rule (no fresh full parity, full host or real-install acceptance run for this release). Local preparation only; no push or deployment authorized.
## Changes

- Notification mail from senders on your allowlist (such as block-explorer alerts) now reaches Agent J too; it only reads it, never replies, and can move an obvious phishing notice to your mailbox's trash.
- On some tablets the system fingerprint screen briefly dropped the connection while Touch ID was being set up, and the setup was lost; it is now handed in after reconnecting, and notices no longer cover a dialog's text and buttons.
- After an upgrade the Claude status line could stop reporting and the quota stopped updating; the upgrade now repairs it, and a reading that is too old shows as out of date on the phone instead of an old percentage.
- Long-press approve on an approval card is back to its original look: the colours stay as they were and the bar fills from left to right while you hold; letting go early rolls it back.
- 邮件白名单里的通知邮件（比如区块浏览器的提醒）现在也会交给 Agent J 阅读；它只读、不会回信，判断是钓鱼通知时可以直接把它移进邮箱的废纸篓。
- 在一些平板上设置 Touch ID 时，系统指纹界面会让网页短暂断线，设置结果因此丢失；现在重新连上后会自动补交，提示也不再挡住弹窗里的文字和按钮。
- 升级之后，Claude 的状态栏可能停止上报、额度一直不刷新；现在升级会自动修好状态栏，读数太旧时手机上会显示「额度读数已过期」，不再显示旧的百分比。
- 审批卡上的长按批准恢复了原来的效果：按住时颜色不变，进度条从左到右走；中途松手会从当前位置退回。

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
