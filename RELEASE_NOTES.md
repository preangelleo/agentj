# Agent J 0.25.0a1 release notes

Host 0.25.0a1 / install 0.27.0; phone-web scope.
Phone parity must pass --strict. Full Android/Telegram parity is incomplete.
Full Android/Telegram parity is incomplete.
No APK is included in this release. Android source is for review, not a released app.

Publication status: local preparation only. Live site verification is DEFERRED until
the public commit and tag are pushed, the site is deployed, and cold installation passes.

On failure restore v0.24.1a2 host/site/phone and the captured Dashboard Worker version (the additive migrations 0032 and 0033 stay in place; they are never re-applied). Release order: C (additive app D1 migration 0032 and feedback D1 migration 0033, then the Dashboard Worker), then A signed host/site/downloads then B phone web immediately after. Normal publication windows remain C 05:00–07:00 JST and A/B 23:00–08:00 JST; outside-window publication requires recorded owner authorization. No relay change. The website support chat, screen help, the web terminal, the Jev safety choice, Bots business settings, friend handover safeguards and the composer / admin-password card fixes ship together. Physical iPhone/Android, WebKit on a real device and the free help-runner deployment are not claimed.
## Changes

- Every agentj.app page now has an Agent J support button at the bottom right, and support.agentj.app opens the same chat: free, with four levels (guest, verified email, signed in, seat holder); a verified email can reach a person, and seat holders can send screenshots. Messages sent too quickly wait in a queue and go out together as one.
- Screen help: when automation stops at a QR code, a verification code or a human check, Agent J puts the code or a full screenshot on your paired phone; you scan, tap "Done" or type the answer, and the work goes on (`agentj qr push`, `agentj screen ask`).
- Web terminal: your computer's command line on the phone under Settings → Safety → Web terminal. Off by default, switched on only at the computer with `agentj terminal enable`, Face ID on every open, closed again by itself.
- Jev safety review is now your choice of TypeSafe, OpenRouter or off, switched under Settings → Safety; setup asks you to pick one.
- Bots business settings: per-bot limits, backup models, conversation retention, source links in answers, editable fixed phrases, and an encrypted support window you can put on your own website with one line.
- Handing tasks between friends is safer: the number of hops is limited, loops are stopped, the rate is capped, and a request nobody picks up expires with a notice; you can also hold one friend's requests until you approve them.
- Pasted text goes by length: if the message box already has words, the paste goes in too, and only when together they pass the single-message limit does the pasted part become an attachment; into an empty box, text over 2,000 characters becomes an attachment.
- After picking a / command or an @ name, one space follows and the cursor sits after it, and the message box keeps the focus; the keyboard only goes away once a message has really been sent.
- The phone's Help and feedback, Emergency repair and offline page now open the support chat directly.
- After you type the admin password, the password card closes as soon as the command starts and becomes a small "sudo running…" strip you can expand for output or stop; only a wrong password keeps the card. Every password and key card has a collapse button and Esc collapses it too; collapsing never declines.
- 官网每一页右下角多了 Agent J 客服按钮，也可以直接打开 support.agentj.app 问：免费，分游客、验证邮箱、已登录、有席位四档；验证邮箱后可以转人工，有席位的用户还能发截图。发得太快时消息会排队，到点合成一条发出。
- 屏幕求助：自动化卡在二维码、验证码或人机验证时，Agent J 把二维码或整屏截图发到你配对好的手机上，你扫码、点「完成」或回一句答案，它接着干（`agentj qr push`、`agentj screen ask`）。
- 网页终端：在手机的「设置 → 安全 → 网页终端」里用电脑的命令行。默认关闭，只能在电脑上运行 `agentj terminal enable` 打开，每次打开都要 Face ID，用完自动关。
- Jev 安全审核可以三选一：TypeSafe、OpenRouter 或关闭，在「设置 → 安全」里切换；安装时也会请你选一种。
- Bots 商用设置：每个 Bot 的额度、备用模型、对话保留期、回答附上来源、可改的固定话术，以及一行代码挂到你自己网站上的加密客服窗口。
- 好友之间转交任务更稳：自动限制转交次数、防止绕圈、限速，没人接的请求到时自动过期并通知你；你也可以让某位好友的请求先等你批准。
- 粘贴文字按长度决定去处：输入框里已经有字就接着贴进去，合起来超过一条消息的上限才把这次粘贴的内容变成附件；输入框空着时，超过 2000 字直接变成附件。
- 选了 / 命令或 @ 之后，后面自动补一个空格，光标停在后面，输入框不会失去焦点；只有消息真正发出去，键盘才收起。
- 手机端设置的「帮助和反馈」、紧急修复和断线页都能直接打开在线客服。
- 输入管理员密码后，命令一开始执行密码卡就收起，变成一条「sudo 执行中…」的小状态条，可以展开看输出或停止；只有密码错了卡片才会留着。每张密码卡和密钥卡都有收起按钮，按 Esc 也能收起，收起不等于拒绝。

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
