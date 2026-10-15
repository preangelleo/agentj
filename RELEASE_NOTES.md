# Agent J 0.22.0a1 release notes

Host 0.22.0a1 / install 0.24.0; phone-web scope.
Phone parity must pass --strict. Full Android/Telegram parity is incomplete.
Full Android/Telegram parity is incomplete.
No APK is included in this release. Android source is for review, not a released app.

Publication status: local preparation only. Live site verification is DEFERRED until
the public commit and tag are pushed, the site is deployed, and cold installation passes.

On failure restore v0.21.1a1. Release order: C (D1 app 0027 + Dashboard) in 05:00–07:00 JST, then A signed host/site/downloads and B phone web right after, in the same 23:00–08:00 window. No relay change.
## Changes

- A new Agent J father-robot logo in five state colours; the site adds a robots & logo page and an official brand kit, and your Telegram bot gets the official avatar when it has none.
- Your own Telegram bot can double as customer service: your private chat still reaches the main conversation, strangers go to the bot you pick; Telegram now pairs with a one-time code.
- Switching the underlying tool (Claude Code / Codex / OpenCode) shows each step on the phone, checks you can switch back first, and hands over through a written handoff; OpenCode works with its free models even without a key.
- Claude login reminders 7, 3 and 1 days before expiry, renewed right from the phone; a failed upgrade says which step and why, and the account page shows the last upgrade.
- The reply card's bottom-left key can run any command or skill; up to five quotes above the message box; a full set of keyboard shortcuts, listed at agentj.app/hotkeys.
- iMessage assistant (Mac only, off by default): codes, deliveries and family texts reach your phone, spam is held back; read-only.
- New installs come with three example workflows (knowledge base, scripts & articles, e-book); where Claude Code / Codex are not offered, a measured Kimi route is recommended.
- Two more underlying tools: Kimi Code, and Alibaba Cloud Bailian Token Plan (an OpenCode preset or Qwen Code); where Claude Code / Codex are not offered a monthly plan is enough, and the Agent explains the plan's terms first.
- Qwen creators can say 「装千问自媒体工具」 to install a kit for images, video and voice-over; the workflow wizard gains a Qwen self-media template.
- The Workflow page counts only real staff; a workflow with only a CEO says so once and helps you add 3–7 staff from its own work.
- Status lines speak in the Agent's own voice and use your chosen form of address; /clear can be undone until your next message.
- Every background job shows a dancing robot, and when one finishes the Agent reads its report and tells you; unread marks sync across all your remotes.
- A rebuilt full-screen reader and no drawn scrollbars anywhere on the phone; every command and code copies with a tap; files go to Telegram in one tap; tap the mic to lock, hold to talk.
- Settings can be searched, Friends and Bots pages have "Let Agent J set this up", the mail app password is set from Settings, and when something is missing the Agent says what and how to add it.
- The weekly allowance in the top bar shows which day of the reset cycle it is, glows faintly blue when you are ahead of the average pace and orange at 80% and 90%; open Usage to see when it resets and where today's pace will land.
- Menu pages share one spacing scale, sub-page headers and skill tabs stay on top; Esc, a swipe from the left edge or Android Back all go back; the menu turns fully dark; the new logo everywhere; bottom sheets never rise above the message card.
- After the service on your computer restarts or upgrades, an open phone turns from grey back to ready within seconds without you sending a message first; when the shared terminal window is closed, the phone shows grey and explains how to reattach.
- A very busy disk no longer stalls the connection; Macs no longer show a false log-in reminder after a restart (the phone's offline page explains instead); Linux / WSL without systemd get a clear fix.
- Fixed forwarded-mail results and duplicates, the Android gesture-bar colour, Markdown colours in replies and the secret pickup card preview.
- The "hand over to another AI account on a quota warning" switch now saves; the Mac home-screen app's "Updated to …" line disappears after 5 seconds.
- Answering a question on the phone in a shared session now releases the terminal too; a momentary rate limit on a Codex API proxy is retried once, and a second failure names the proxy; a slow Codex start during a switch is waited for, and a failure says why and what next.
- 新的 Agent J 爸爸机器人 Logo，五种状态五种颜色；官网新增机器人与 Logo 页和官方品牌套件，你的 Telegram 机器人没有头像时自动换上官方头像。
- 你自己的 Telegram 机器人可以兼做客服：你的私聊照旧进主对话，陌生人交给你选的客服 Bot；Telegram 改用一次性配对码绑定。
- 切换底层工具（Claude Code / Codex / OpenCode）时手机上一步步显示进度，切之前先确认切得回来，切前自动写交接、切过去先读交接；OpenCode 没有 key 也能用它的免费模型。
- Claude 登录到期前 7、3、1 天提醒，直接在手机上续期；升级失败会说清哪一步、什么原因，账户页能看到最近一次升级。
- 回复卡左下角的快捷键可以换成任何命令或技能；输入框上方最多同时放 5 段引用；全套键盘快捷键，官网 agentj.app/hotkeys 有完整列表。
- iMessage 助手（仅 Mac，默认关）：验证码、快递、家人等短信推到手机，垃圾短信挡掉，只读不改。
- 新装时预置三套示例工作流（知识库、脚本与文章、电子书）；用不了 Claude Code / Codex 的地区按实测推荐 Kimi 路线。
- 多两种底层工具：Kimi Code，和阿里云百炼 Token Plan（OpenCode 预设或 Qwen Code）；用不了 Claude Code / Codex 的地区按月订阅就能用，Agent 会先讲清套餐条款。
- 千问创作者说「装千问自媒体工具」就能装上生图、出视频、配音的工具箱；工作流向导多了「千问自媒体一条龙」模板。
- 工作流页只把真正的员工算作员工；只有 CEO 的工作流会提示一次，并按它的工作内容帮你补 3–7 名员工。
- 状态提示改成 Agent 自己的口吻，称呼跟着「对你的称呼」设置走；/clear 在下一条消息前都能撤销。
- 后台在干的活一律有跳舞机器人显示，后台任务结束后 Agent 自动读报告向你汇报；未读在你的所有遥控器之间同步。
- 全屏阅读重做，整个手机网页不再画滚动条；所有命令和代码一点就复制；文件卡一键发到 Telegram，麦克风单击锁定、长按说话。
- 设置页可以搜索，好友页、客服 Bot 页有「让 Agent J 帮我设置」；邮箱授权码在设置页就能填；能力不够时 Agent 会说清差什么、怎么补。
- 顶栏本周额度后面写着重置周期的第几天，用得比平均快会微微发蓝光，用到 80%、90% 发橙光；点开「用量」能看到还有多久重置、按现在的速度到重置时大概用到多少。
- 菜单页间距统一，子页面页眉和技能页标签固定在顶部；Esc、左边缘右滑、安卓返回键都能退出；菜单打开时整页深色；全站换新 Logo；底部弹窗最高只到消息卡顶部。
- 电脑上的服务重启或升级后，开着的手机几秒内就从灰色恢复，不用先发一条消息；共享会话的终端窗口关了时手机会显示灰色并说明怎么接回。
- 电脑磁盘很忙时不再卡住断线；Mac 重启后不再误弹登录提醒，改由手机离线页说明；没有 systemd 的 Linux / WSL 会给出修法。
- 修复转发邮件的结果与防重复、Android 底部手势条颜色、回复里的 Markdown 颜色和密钥领取卡的预览。
- 「额度告警时交给其他 AI 账号」开关现在能保存；Mac 主屏幕 App 的「已更新到…」提示 5 秒后自动消失。
- 共享会话里在手机上回答提问，电脑终端也会放行；Codex 中转偶发限流时自动重试一次，还失败会说清是哪家中转；切到 Codex 时启动慢会多等一会，失败会说清原因和下一步。

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
