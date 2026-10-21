# Agent J 0.25.2a1 release notes

Host 0.25.2a1 / install 0.27.2; phone-web scope.
Phone parity must pass --strict. Full Android/Telegram parity is incomplete.
Full Android/Telegram parity is incomplete.
No APK is included in this release. Android source is for review, not a released app.

Publication status: local preparation only. Live site verification is DEFERRED until
the public commit and tag are pushed, the site is deployed, and cold installation passes.

On failure restore v0.25.0a1 host/site/phone and the captured Dashboard Worker version (the additive app migrations 0034 and 0035 stay in place; it is never re-applied). Release order: C (additive app D1 migrations 0034 and 0035, then the Dashboard Worker; feedback database unchanged), then A signed host/site/downloads then B phone web immediately after. Normal publication windows remain C 05:00–07:00 JST and A/B 23:00–08:00 JST; outside-window publication requires recorded owner authorization. No relay change. The one-line message box with a full-screen editor, the Calendar card, chat ! commands, the computer password filled in with Face ID, the full-screen web terminal and direct Notebook / To-do saves ship together. Physical iPhone/Android and WebKit on a real device are not claimed.
## Changes

- Calendar saves directly; ICS and Google/iCloud sync keep conflicts recoverable.
- Prefix chat with ! for terminal commands; output masks suspected passwords/keys.
- Face ID fills sudo passwords, stored encrypted on your computer.
- One-line input; fifth button, Ctrl+G or upward hold opens full-screen editing.
- Optional keep-awake on plugged-in Mac/Linux. Linux runs with lid closed or after reboot before login; Mac lid needs approval. Shut down for travel; keep-awake off restores settings.
- My devices groups confirmed same-owner computers; @ addresses their main Agent. friends pick recommends by load/usage.
- Skills show last use/count, archive/restore; three similar tasks prompt a skill suggestion.
- Resume a multi-step automation at the failed step.
- Optional Files/Obsidian cards show chosen folders/notes; install cards from Plaza too.
- First pairing shows a six-digit comparison code automatically; terminal includes QR and link.
- Notebook/To-do save directly; Ask Agent J to collaborate starts AI help.
- Computer: Settings → Security → Web terminal, Face ID, computer password once. Full-screen; Esc folds it while running.
- The / list includes Claude Code, Codex and OpenCode commands.
- Alt+Shift (Mac ⌥⇧) plus a card letter opens it; any background colour.
- Optional palette card creates matching colours for card backgrounds.
- Pull past the top to refresh; drafts stay. Phone safe areas stay dark.
- Question cards accept typed answers.
- Plaza checks safety/signatures; extra permissions need phone approval.
- OpenCode sub-agents follow main-chat rules; approvals reach the phone.
- Auto-update off and two versions behind: manual reminders at 09:00 Japan time.
- Mixed-up Ark keys: Use Agent Plan and retry uses the saved correct key.
- Support opens the same chat in a new tab; answers start with 2–4 lines and widen.
- Replies show flowcharts/charts/steps; forms only prepare drafts.
- First-connection guidance covers extra remotes, owners, seat holders and managed accounts.
- Copy shows ✓ Copied; reader stays open and information notices fade.
- Chat quota guidance starts with switching AI; background handover is explained separately.
- Option groups in one reply work independently.
- Stop has the same label in both languages.
- Terminal history skips suspected password/key-setting commands.
- One reply no longer appears on two pages.
- Host recovery clears stalled banners and sends queued messages.
- Files send after entering subfolders; paths/$VAR are not keys.
- AI-confirmed quota wins; old phone readings get a stale warning.
- Custom data folders no longer break updates; missing model is not a bad key.
- OpenCode reconnects after upgrades, keeping the conversation.
- Stop suppresses repeated Esc; quota notices cover only the active coding tool.
- Saved keys refresh. Bailian: Token Plan/pay as you go; retired Coding Plan keys stay saved with a migration notice.
- Approvals/sudo stay inside replies; reading safe areas stay dark.
- Same owner: start on one side, compare on any authorized remote.
- Verification preserves support routing; timeout fallback does not use a successful-answer allowance.
- Holding icons avoids nearby text selection; hover/press/focus/disabled labels stay readable.
- Promo-code errors distinguish unknown/expired/exhausted codes; uncertain reasons stay neutral.
- The web-report glyph stays visible in normal, hover, pressed, focused and disabled states.
- 日历直接存日程；支持只读 ICS、Google/iCloud 双向同步，冲突保留你的版本。
- 以 `!` 开头（如 `!df -h`）在网页终端运行；结果卡和遮住疑似密码、密钥的输出交给 Agent。
- sudo 卡上点「用 Face ID 填入电脑密码」，不用每次手打；密码只以加密形式存在电脑上。
- 输入框永远一行；长内容点上方第 5 个按钮或按 Ctrl+G 全屏编辑，手机上按住输入框上滑也行。
- 安装可选「永不休眠」：插电 Mac/Linux 不睡，Linux 合盖、重启未登录仍运行；Mac 合盖另确认。出门请关机，keep-awake off 还原。
- 「我的设备」群：确认过同一个主人的几台电脑自动成群，@ 哪台就交给哪台的主 Agent；`agentj friends pick` 按负载和额度推荐一台。
- 技能显示最近使用时间和次数，可归档恢复；同类事情做满 3 次，Agent 会问是否存为技能。
- 分步自动化某一步失败时，可以从那一步继续，做完的步骤不重跑。
- 文件和 Obsidian 卡（默认关闭）：在手机查看你选定的文件夹和笔记；也能安装广场上的卡片。
- 首次配对更清楚：账号页扫码后自动显示六位确认码，核对一致再批准；终端同时给二维码和电脑链接。
- 记事本和待办点 + 直接存到电脑，不经过 AI；想让 Agent 整理，点「请 Agent J 协作」。
- 电脑浏览器：「设置→安全→网页终端」开启，Face ID 确认后输入一次电脑密码；全屏打开，Esc 收起继续运行。
- 打 `/` 时也列出你用的 AI 编程工具（Claude Code、Codex 或 OpenCode）自己的命令。
- 电脑上按 Alt+Shift（Mac 是 ⌥⇧）加卡片英文名的一个字母直接打开卡片；卡片底色任何颜色都能选。
- 新的配色板卡（默认关闭）：一个颜色生成整组搭配色，点「存到我的配色」后每张卡片的底色都能用。
- 消息滚到顶部继续下拉，看到「松开刷新」松手就刷新，草稿保留；手机顶部状态栏固定深色。
- 问答卡里可以直接写答案，点「确认」就发回电脑。
- 从广场装技能前先做安全检查、核对发布者签名；要的权限超出允许范围时，先在手机上问你。
- OpenCode 派出的子 Agent 守和主对话同样的规则，审批照样到手机。
- 关掉自动升级的电脑落后至少两个版本时，每天 09:00（日本时间）收到手动升级提醒。
- 方舟 Agent Plan 和 Coding Plan 的 key 配反时，点「改用 Agent Plan 并重试」就用已存的 key 改好。
- 官网客服在新标签延续对话，回答先给 2–4 行结论；正文跟着窗口变宽。
- 回复里能直接看流程图、图表和步骤条；表单只帮你准备草稿。
- 首次连上提示怎样添加手机或常用电脑；安装说明区分自己的账号、只有席位和别人管理的账号。
- 拷贝按钮短暂显示 ✓「已复制」，阅读页拷贝后继续阅读；纯告知提示自动消失。
- 聊天额度用完先指引切换 AI，后台任务的自动转交另有说明。
- 一条回复里的几组选项按钮各点各的，互不影响。
- 停止按钮中英文都显示「Stop」。
- 网页终端的命令历史不记像设置密钥或密码的行。
- 同一条回复不再出现两页。
- 主机恢复后，没响应的横条自动消失，排队的消息自动补发。
- Agent 进子文件夹后文件照常发到手机；路径和 `$VAR` 不再被当成密钥拦下。
- AI 确认额度用完时以它为准，手机上没满的旧读数标「读数可能过期」。
- 自定义过数据目录的电脑不再误报升级失败；找不到模型时不再说 Key 失效。
- OpenCode 升级后自动重连，对话保留。
- Stop 防止短时间重复发送 Esc；额度提醒只看当前主 AI 工具。
- 密钥保存后立即刷新；百炼只提供 Token Plan 和按量付费入口，旧 Coding Plan 只提示迁移，不删除密钥。
- 审批与 sudo 都显示在回复卡内部；阅读页上下固定深色。
- 同一主人：一边看码，另一边有权遥控器核对确认。
- 验证邮箱前后客服线路不变；上游超时给出明确回退，不扣成功回答次数。
- 长按图标不再选中周边控件；按钮在悬停、按下、聚焦和暂不可用时保持清晰。
- 优惠码提示区分不认识、过期、次数用完；原因不确定时给出中性提示。
- 网页报告图案在正常、悬停、按下、聚焦和暂不可用时都保持清晰。

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
