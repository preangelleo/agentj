# Agent J 0.23.0a1 release notes

Host 0.23.0a1 / install 0.25.0; phone-web scope.
Phone parity must pass --strict. Full Android/Telegram parity is incomplete.
Full Android/Telegram parity is incomplete.
No APK is included in this release. Android source is for review, not a released app.

Publication status: local preparation only. Live site verification is DEFERRED until
the public commit and tag are pushed, the site is deployed, and cold installation passes.

On failure restore v0.22.2a1. Release order: C (Dashboard + additive app migration 0028) in 05:00–07:00 JST, then A signed host/site/downloads and B phone web right after, in the same 23:00–08:00 window. No relay change.
## Changes

- Emergency repair in the phone's Settings: reset the connection in one tap (reconnect → recover the conversation → a new one if needed, undoable → restart Agent J), ask Agent J Father for remote help, sign an AI account back in; a rescue helper takes over when Agent J on the computer is stuck.
- Three approval levels: Safe / Standard (default, as before) / Full; optionally no cards once a goal is set. With no remote online, a card waits 30 minutes and you get a one-line reminder on Telegram or e-mail.
- Models & API keys page: every key on one page, only the last 4 characters shown; copy, edit and delete need Face ID, the Agent never sees a value.
- `agentj mail send` sends formatted e-mail by default, a web report can be the whole body, with Cc and Bcc.
- DeepSeek Harness support (the Lares engine that comes with Olares), and an Olares install guide on the site.
- Agent J is open source (Apache-2.0); the site adds pages per model platform (Volcano Ark, Kimi, Bailian, DeepSeek) and site-wide search.
- An isolated Agent can switch its coding tool by itself without the administrator password; goal cards are in plain words, and you can add a permission mid-goal.
- Coding plans such as Volcano Ark and Bailian can run your own scheduled tasks; a support bot picks its provider first, then takes its own pay-as-you-go key.
- Recording blurs the whole page in the current status colour; a locked take has three buttons: cancel, stop, stop and send. The shortcut sheet has search; `/new` is the same as `/clear`.
- A long-press screenshot also goes to the clipboard; order the composer buttons yourself, synced across remotes; the audio player fills the file card; several quotes fold into one row.
- Mac Login Items show "Agent J" with the robot icon instead of an unidentified developer.
- When you reply to a Telegram message, the Agent sees the message you quoted.
- Security fix: with OpenCode 2.x in the high-risk reminder mode, subagent rules did not take effect; fixed.
- On an older page the new-message alert is no longer cleared by another remote's read mark.
- Shared sessions remember their binding per coding tool; when a Codex conversation is gone, the latest one is picked up again.
- A silent heartbeat turn no longer pushes its progress note to the phone.
- "Cancel at period end" on the account page now reaches the payment provider and no longer shows "renews".
- Commands on system cards are no longer shown as math.
- 手机设置里新增「紧急修复」：一键重置连接（重连 → 找回对话 → 必要时开新对话、可撤销 → 重启 Agent J）、请 Agent J Father 远程帮忙、AI 账号登录失效时重新登录；电脑上的 Agent J 卡住时也有救援守护接手。
- 审批分三档：安全 / 中等（默认，和以前一样）/ 全放行；可以打开「设了目标就不弹窗」。遥控器都不在线时，卡片留 30 分钟，并给你的 Telegram 或邮箱发一句提醒。
- 「模型和 API Key」页：所有 Key 一页管理，只显示后 4 位，拷贝、修改、删除都要先过 Face ID，Agent 看不到值。
- `agentj mail send` 默认发带格式的邮件，网页报告可以整页当正文发出去，支持抄送和密送。
- 支持 DeepSeek Harness（Olares 自带的 Lares 引擎），官网有 Olares 安装说明。
- Agent J 开源了（Apache-2.0），官网新增按模型平台分的页面（火山方舟、Kimi、百炼、DeepSeek）和全站搜索。
- 隔离里的 Agent 也能自己切换底层工具，不用再输管理员密码；目标模式的卡片改成大白话，目标进行中可以追加授权。
- 火山方舟、百炼等编程套餐可以跑你自己的定时任务；客服 Bot 先选服务商，再填它自己的按量 Key。
- 录音时整页按当前状态色模糊，锁定后三个按钮：取消、停止、停止并发送；快捷键表可以搜索；`/new` 等同 `/clear`。
- 长按截屏同时进剪贴板；输入框按钮顺序可以自己排，多台遥控器同步；文件卡里的音频播放器撑满卡片；引用多段时折成一行。
- Mac 的登录项显示「Agent J」和机器人图标，不再是「身份不明的开发者」。
- Telegram 里回复某条消息时，Agent 能看到你引用的那条。
- 安全修复：OpenCode 2.x 高风险提醒模式下，子 Agent 规则此前未生效，已修。
- 停在旧页时，新消息的提醒不再被别的遥控器的已读吞掉。
- 共享会话按每个底层工具各自记绑定；Codex 绑定的对话没了会自动接回最近那个。
- 〔不回群〕的心跳回合不再把过程说明推到手机上。
- 账户页「到期取消」现在会真的同步到付款方，不再显示「续费」。
- 系统卡片里的命令不再被当成数学公式显示。

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
