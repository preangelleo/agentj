# Agent J 0.26.8a2 release notes

Host 0.26.8a2 / install 0.28.9; phone-web scope.
Phone parity must pass --strict. Full Android/Telegram parity is incomplete.
Full Android/Telegram parity is incomplete.
No APK is included in this release. Android source is for review, not a released app.

Publication status: local preparation only. Live site verification is DEFERRED until
the public commit and tag are pushed, the site is deployed, and cold installation passes.

Scope: host hotfix — background completions read their own report back, the shared file card after a restart, quiet old owner-check records, a same-owner same-host source tier, the OpenCode 2.x sign-in check, the fenced upgrade's service restart run by the host, Claude account switches delivered to the main conversation, shared Codex no longer waiting on a dead desktop turn or a dead turn held by an idle Codex daemon, plaza installs no longer blocked by missing dependencies (agentj plaza verify), no Face ID for work dispatched to the owner's own workflow CEO session once the host has verified the AI tool, read-only AI tool install folders in the Agent's restricted environment (self-updates through the host, /update), and a heartbeat that no longer pauses for context. Release order: C Dashboard support knowledge, then A signed host/site/downloads, immediately followed by B phone web. No new D1 migration or relay change. Preserve all existing security journals and the published 0.26.7/0.26.8a1 rollback downloads.
## Changes

- [all] Same-owner, same-computer sources get their own tier: host notices, background-task completions and hand-offs from Agent sessions this host started do ordinary work without a card; payments, deletions, outbound sends, credential and price changes still need your remote's approval and queue while you are away. External sources stay data only.
- [all] Work dispatched to your own workflow CEO session no longer asks for Face ID when the host has verified that AI tool; AI tool install folders are read-only inside the Agent's restricted environment, and AI tool self-updates go through the host: send /update from your remote.
- [all] The heartbeat no longer pauses for context use and no longer asks you to /compact; past half the window the main Agent tidies its own context.
- [codex,claude] When a background task your conversation started (agentj busy run / busy start, Linux hosts) finishes, the main Agent reads its own report back and reports to you, no longer refused as an unverified source and without you asking for the result; a real refusal shows one sentence and a See result button.
- [claude] A standalone agentj file show in a shared Claude session attaches its file card after a host upgrade or restart too; when it really cannot attach, it asks you to /clear that session on the computer or start a new one.
- [all] Old pending owner-check background records: those already confirmed finished settle by themselves and reconcile-only ones no longer remind you; the original records are kept and still listed by /recover-background.
- [opencode] The AI sign-in rescue's Check now passes for OpenCode 2.x and free models; an incompatible CLI or a configuration error is told apart from being signed out or out of quota instead of always asking you to sign in again.
- [codex] When Codex upgrades the host inside its restricted environment, the host now restarts the service outside it, so the upgrade no longer rolls back for that reason; a failure names the error number, command, step and time. If an upgrade has trouble, send /update from your remote and the host upgrades directly.
- [claude] After switching Claude accounts or finishing a sign-in rescue, the new account reaches the main conversation and is checked in its own environment before success is shown; a session you opened in your own terminal gets a Reopen AI session card.
- [codex] Shared Codex sessions: when the desktop turn's process is gone (for example force-quit), Agent J stops waiting for it and handles messages from your remote after about two minutes; the sign-in rescue skips Codex's Hooks need review start screen without trusting any hook.
- [codex] When the Codex daemon is idle, a dead turn it still holds is released so your remote's messages no longer wait behind it; agentj doctor reports when the Codex CLI and daemon versions differ; the terminal-command list no longer fails to open.
- [all] Missing dependencies no longer block a plaza install: the status shows Installed · dependencies pending, and once they are in place agentj plaza verify reruns the self-check without signing again.
- [all] 同一主人、同一台电脑上的来源单独成一档：主机系统提示、后台任务完成通知、本主机启动的 Agent 会话发来的交接，可以直接做普通工作，不再弹卡或被拦；付款、删除、对外发送、改凭据、改价仍要在遥控器上批准，你不在线时只排队、不挡其他工作。外部来源仍只当作资料。
- [all] 派给你自己工作流 CEO 会话的工作，在主机已核验该 AI 工具时不再要求 Face ID；Agent 受限环境里 AI 工具的安装目录改为只读，AI 工具自身的升级改由主机完成，在遥控器上发送 /update 即可。
- [all] 心跳不再因为上下文占用而暂停，也不再提示你手动 /compact；上下文用过一半时，由主 Agent 自己整理。
- [codex,claude] 由你的对话发起的后台任务（agentj busy run / busy start，Linux 主机）完成后，主 Agent 会直接读回它自己的报告并向你汇报，不再被「未核验来源」拦下，也不用你手动去要结果；真被拦时只显示一句说明和「看结果」按钮。
- [claude] 共享 Claude 会话里单独运行 agentj file show，在主机升级或重启后也能把文件卡发到页面；确实接不上时会提示你在本机对这个会话 /clear 或开新会话。
- [all] 旧的「待本人核对」后台记录：已经确认完成的会自动收讫，只用于对账的不再提醒；原始记录保留，仍可用 /recover-background 查看。
- [opencode] OpenCode 2.x 和免费模型的 AI 登录救援「现在检查」可以通过；命令行不兼容或配置错误与未登录、额度不足分开判断，不再一律要求重新登录。
- [codex] Codex 在受限环境里执行主机升级时，服务重启改由主机在受限环境外完成，不再因此失败回滚；失败时会写明错误号、命令、阶段和时间。升级遇到问题时，在遥控器上发送 /update，由主机直接升级。
- [claude] 切换 Claude 账号或完成登录救援后，新账号会送到主对话，并在主对话自己的环境里核实生效后才显示成功；你自己在终端里开的会话会收到「重开 AI 会话」卡片。
- [codex] 共享 Codex 会话：桌面端那一轮的进程已经不在（例如被强制结束）时，Agent J 不再一直等它，约两分钟后照常处理遥控器发来的消息；登录救援会跳过 Codex 的「Hooks need review」启动画面，不信任任何钩子。
- [codex] Codex 后台进程空闲时，它还占着的已失效回合会被释放，遥控器消息不再一直排在它后面；agentj doctor 会指出 Codex 命令行与后台进程版本不一致；终端指令列表不再报错打不开。
- [all] 广场技能缺少依赖时不再挡住安装：状态显示「已安装·依赖待补」，依赖补齐后运行 agentj plaza verify 重新自检，不用重新签名。

## Material acceptance limits

- Signed Linux package tests and stand-in services do not establish customer upgrades, a real second Claude account on macOS or real desktop Codex sessions. Background-completion read-back applies to Linux hosts; shared desktop sessions, systemd units and other platforms keep /recover-background. Olares chart and image are unchanged in this release.

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
