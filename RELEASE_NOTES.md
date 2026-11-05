# Agent J 0.26.9 release notes

Host 0.26.9 / install 0.28.10; phone-web scope.
Phone parity must pass --strict. Full Android/Telegram parity is incomplete.
Full Android/Telegram parity is incomplete.
No APK is included in this release. Android source is for review, not a released app.

Publication status: local preparation only. Live site verification is DEFERRED until
the public commit and tag are pushed, the site is deployed, and cold installation passes.

Scope: a stuck AI turn recovers by itself in the conversation the owner is using (with force-end and take-over buttons), a real full terminal in the web app, signing the AI tool in again from the chat with the result checked in the main conversation, an official remote help session the owner grants for a fixed time, Codex replies to a newer message in a new reply card, every card the remote pops placed under the reply card, fewer confirmations (the owner's request is the authorisation; a spending budget), the sudo card's 'remember with Face ID' that really remembers, plaza self-checks that see Homebrew tools and the declared Python, one install line per plaza install, silent successful admin commands, a clear fix line when the program folder belongs to another account, and OpenCode always the newest 2.x. Release order: C Dashboard support knowledge, then A signed host/site/downloads, immediately followed by B phone web. No new D1 migration or relay change. Preserve all existing security journals and the published 0.26.7/0.26.8a1/0.26.8a2 rollback downloads.
## Changes

- [all] When an AI turn gets stuck, the host recovers it by itself in the conversation you are using and tells you so; the queued notice shows how many minutes you have waited with a Force-end this turn button, and emergency repair gains Take over the stuck session.
- [all] The web terminal is now a full terminal: vim, top, claude and other interactive programs display and work properly and follow your phone's screen size; dangerous commands still need your signed confirmation.
- [claude,codex] When a reply says the AI tool is signed out, a button under it lets you sign in again right in the chat (paste the authorisation code); success shows only after the host checks it in your main conversation.
- [all] Emergency repair can ask Agent J official remote help: grant 15, 30 or 60 minutes with Face ID; meanwhile the official side can only send tasks to your Agent, which run under your existing limits and approvals; a banner lets you take it back at any time, it expires by itself, and everything is logged.
- [all] Fewer interruptions: what you ask for is the authorisation, so the Agent does the follow-up steps without asking you to reply on / confirm / send again; only spending beyond your budget and showing a key need Face ID. Set a daily and monthly budget in Settings → Security → Budget.
- [all] Every card your remote pops now appears under the reply card, never from the message box, and says why it is asking you.
- [all] The main Agent shares your load: it designs and supervises the duties and acceptance of your workflow CEOs and sub-Agents instead of doing the work itself, and it checks finished background work instead of passing that to you.
- [codex] When you send a message during a long Codex answer, it goes straight into the current turn; the rest of the answer lands in a new card at the bottom and the old card says Continued below.
- [all] Ticking Remember with Face ID on the sudo card now really remembers: next time the same kind of command needs Face ID only.
- [all] Plaza skill and workflow self-checks can use Homebrew tools such as node and ffmpeg and the Python version a package declares, and accept Google Chrome; a package missing its self-check file installs with the self-check skipped.
- [all] A plaza install sends you one message: no early not installed while the first self-check failed and the Agent is still adding dependencies; the same line turns into Installed once they are in.
- [all] Successful admin commands no longer post Admin command finished; only a failure says, in one sentence, which thing failed.
- [all] When the program folder belongs to another account (installed with sudo before), the upgrade says why, changes nothing and gives one fix line (an admin password card); agentj doctor gains a program folder row; failed downloads are retried automatically.
- [opencode] Installing or upgrading OpenCode always picks the newest 2.x; a 1.x install gets the upgrade command.
- [all] After a successful upgrade inside the macOS restricted environment, agentj doctor no longer reports the upgrade as failed.
- [all] When the last check of a big file upload fails, the page now says so instead of hanging.
- [all] AI 一轮回答卡住时，主机会在你正在用的这个对话里自动恢复，并告诉你「刚才卡住了，已自动恢复」；排队提示显示已等几分钟，可以点「强制结束这一轮」，紧急修复里多了「接管卡住的会话」。
- [all] 网页终端升级为完整终端：vim、top、claude 等交互程序能正常显示和操作，大小跟着手机屏幕变；危险命令仍要你签名确认。
- [claude,codex] 回复里提示没登录或登录失效时，下面有「重新登录」按钮，就在聊天页登录、粘贴授权码；主机在你正在用的主对话里核实生效后才显示成功。
- [all] 紧急修复里可以请 Agent J 官方远程协助：Face ID 授权 15、30 或 60 分钟，期间官方只能给你的 Agent 发任务，照你原有的限制和审批执行，顶部横幅可随时收回，到期自动失效，全程留档。
- [all] 少打扰：你交代的事就是授权，后续步骤 Agent 直接做，不再要你回「开」「确认」「再发」；只有预算外花钱、显示密钥这类才要 Face ID。设置 → 安全 → 预算 可以设每日、每月预算。
- [all] 遥控器弹出的卡片统一出现在回复卡下方，不再从输入框那边弹出；卡片会写明为什么要问你。
- [all] 主 Agent 替你分担：它设计并监督各工作流 CEO 和子 Agent 的分工与验收，自己不下场干活；后台活完成后由它核对，不推给你。
- [codex] Codex 长回答进行中你又发了消息时，消息直接插进当前这一轮；之后的回答放到底部新卡片里，旧卡片写「继续见下方」。
- [all] sudo 卡勾选「用 Face ID 记住」现在真的会记住，下次同类命令只要 Face ID。
- [all] 广场技能和工作流的安装自检能用上 Homebrew 装的 node、ffmpeg 等工具和包声明的 Python 版本，也认 Google Chrome；包里缺自检文件时跳过自检照常安装。
- [all] 广场安装只给你一条消息：第一次自检没过、Agent 还在补依赖时不再先报「没装」，补齐后同一行改成「已装好」。
- [all] 管理员命令成功时不再发「管理员命令已完成」，只在失败时用一句话说明是哪件事。
- [all] 程序目录属于另一个账号（以前用 sudo 装过）时，升级会说清原因、什么都不动，并给出一行修复（弹管理员密码卡）；agentj doctor 多了程序目录一行；下载失败会自动重试。
- [opencode] 安装或升级 OpenCode 一律装最新 2.x；装的是 1.x 时会提示升级命令。
- [all] macOS 受限环境里升级成功后，agentj doctor 不再误报升级失败。
- [all] 上传大文件时最后一步校验失败会直接提示失败，不再一直卡住。

## Material acceptance limits

- Signed Linux package tests and stand-in services do not establish customer upgrades, real desktop Codex or Claude sessions, a real macOS sudo card or a real remote help session. Olares chart and image are unchanged in this release.

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
