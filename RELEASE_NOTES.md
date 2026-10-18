# Agent J 0.23.2a1 release notes

Host 0.23.2a1 / install 0.25.2; phone-web scope.
Phone parity must pass --strict. Full Android/Telegram parity is incomplete.
Full Android/Telegram parity is incomplete.
No APK is included in this release. Android source is for review, not a released app.

Publication status: local preparation only. Live site verification is DEFERRED until
the public commit and tag are pushed, the site is deployed, and cold installation passes.

On failure restore v0.23.1a1 host/site/phone and the captured Dashboard Worker version. Release order: C (Dashboard Worker only, no D1 migration), then A signed host/site/downloads then B phone web immediately after. Normal publication windows remain C 05:00–07:00 JST and A/B 23:00–08:00 JST; emergency overrides require the recorded owner authorization. No relay change. This combined hotfix includes P379 updater recovery, P380 commands/todo/clear/Daily AI News fixes, #86 iOS safe areas and #102 pairing-page guidance. A preserves the already-published P381–P383 website content, checked against the P383 publication receipt. Older clients missing the installer must recover it locally before upgrading.
## Changes

- The unpaired page links to the official website to learn more and sign up.
- Resolve persistent installers before updates; include redacted installer failures and local recovery commands.
- Allow Daily AI News settings for hosts bound through owner installation codes.
- Dismiss host command results, wrap long to-dos and fold resume commands into the original clear-success card.
- Keep iPhone and iPad top and bottom safe areas dark while chat status colours continue to work.
- 未配对页底部新增官网了解和注册入口。
- 修复自动升级找不到安装器时安装和回滚连续失败；失败报告保留脱敏安装输出，回滚失败时给主人本地恢复命令。
- 修复自用安装码绑定的主机无法开关 Daily AI News。
- 主机命令结果可关闭，长待办正常换行，清空成功卡默认收起续聊命令。
- iPhone 和 iPad 顶部、底部安全区固定为深色，聊天状态色保持原有显示。

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
