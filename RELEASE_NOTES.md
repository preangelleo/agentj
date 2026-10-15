# Agent J 0.22.1a1 release notes

Host 0.22.1a1 / install 0.24.1; phone-web scope.
Phone parity must pass --strict. Full Android/Telegram parity is incomplete.
Full Android/Telegram parity is incomplete.
No APK is included in this release. Android source is for review, not a released app.

Publication status: local preparation only. Live site verification is DEFERRED until
the public commit and tag are pushed, the site is deployed, and cold installation passes.

On failure restore v0.22.0a1. Release order: C (Dashboard only, no D1) in 05:00–07:00 JST, then A signed host/site/downloads and B phone web right after, in the same 23:00–08:00 window. No relay change.
## Changes

- No restart after changing settings: tap Reload configuration in ≡ All commands, send /reload, press Alt+Shift+L, or run agentj reload on the computer; the Agent picks up the new settings before your next message and the conversation stays.
- When the quota runs out, work continues by itself at the reset time: a banner says when, and Cancel the automatic continue stops it.
- Whatever runs on your computer (Claude Code, Codex, OpenCode, Kimi or Qwen), the Agent can show you a choice card on the phone.
- A Volcengine Ark Coding Plan preset for mainland China: agentj provider ark connects it in one step.
- Every notice on the phone is one dark pop-up that tells success, warning, error and progress apart at a glance; the empty message box says Message… and shows what is left near the length limit.
- Alt+Shift+A replies to the current message from anywhere, with the focus left in the message box; the upgrade receipt reads more clearly.
- Read marks sync more reliably across all your remotes: after an upgrade existing replies count as read, so nothing opens at 99+.
- The website and account page have clearer dark-mode layers and a new footer line.
- A message you just sent is never run twice when the computer restarts or upgrades; one that did not finish is marked, so you can send it again if needed.
- When the computer cannot reach the connection service, status and doctor now say where it is stuck (network, sign-in, seat) instead of connecting forever.
- macOS Login Items no longer fill up with agentj entries; the old ones are removed on upgrade.
- Switching a shared session to OpenCode on a Mac no longer idles and rolls back; asking for the same security setting twice shows one card, applied with the newest value once you allow it.
- Old configuration files with dotted keys such as "agent.xxx" no longer turn into a configuration error after the upgrade (that spelling never took effect; nest the key to use it).
- 改完设置不用重启：在 ≡ 全部命令里点「重新加载配置」、发 /reload、按 Alt+Shift+L，或在电脑上运行 agentj reload，Agent 在下一条消息前换上新设置，对话保留。
- 额度用完后，到重置时间自动接着干：页面上方写着「额度用完，预计几点自动继续」，不想等可以点「取消自动继续」。
- 不管电脑上用的是 Claude Code、Codex、OpenCode、Kimi 还是 Qwen，Agent 都能在手机上弹选择卡让你点选。
- 新增火山方舟 Coding Plan 预设（中国大陆），用 agentj provider ark 一步接上。
- 全站提示统一成深色弹窗：成功、提醒、出错、进行中一眼分清；输入框里没字时写着「说点什么…」，快到字数上限会显示还剩多少。
- Alt+Shift+A 在任何地方都能回复当前这条，焦点留在输入框；升级完成的回执说得更清楚。
- 已读在你所有遥控器之间同步更稳：升级后已有的回复都算已读，不会一打开就是 99+。
- 官网和账户页暗色更有层次，页脚换成新标语。
- 电脑重启或升级时，手机上刚发的消息不会被执行两遍；没办完的会标一句，需要请再发一次。
- 电脑一直连不上中继时，状态和体检会说清卡在哪一步（网络、认证、席位等），不再只是一直「连接中」。
- Mac「登录项与扩展」里不再堆出一排 agentj，升级后旧的会自动清掉。
- Mac 上共享会话切到 OpenCode 不再空转后回滚；同一项安全设置连着申请两次，只弹一张卡，批准后按最新设置生效。
- 旧配置文件里写成「agent.xxx」这种带点的键，升级后不会报配置错误（这种写法从来不生效，要用请写成嵌套的形式）。

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
