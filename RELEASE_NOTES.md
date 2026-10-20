# Agent J 0.24.1a1 release notes

Host 0.24.1a1 / install 0.26.1; phone-web scope.
Phone parity must pass --strict. Full Android/Telegram parity is incomplete.
Full Android/Telegram parity is incomplete.
No APK is included in this release. Android source is for review, not a released app.

Publication status: local preparation only. Live site verification is DEFERRED until
the public commit and tag are pushed, the site is deployed, and cold installation passes.

On failure restore v0.24.0a1 host/site/phone and the captured Dashboard Worker version (the additive app migration 0031 stays in place; it is never re-applied). Release order: C (additive app D1 migration 0031, then the Dashboard Worker; feedback database unchanged), then A signed host/site/downloads then B phone web immediately after. Normal publication windows remain C 05:00–07:00 JST and A/B 23:00–08:00 JST; outside-window publication requires recorded owner authorization. No relay change. Page search, the five-second reconnect rule, the optional 10-minute admin approval, passphrase reset from a paired phone, billing and seat-binding fixes, a shared speech model and upgrade check names ship together. Native Windows, LG TV and physical iOS acceptance are not claimed.
## Changes

- Search at the top right of every page searches that page: card pages filter their records in place, and a Settings result opens the right item and flashes it.
- Admin command cards have an optional switch (off by default; you tick it yourself): for 10 minutes, read-only commands in the same format and the same folder are not asked again; shells and scripts always ask, and you can revoke it at any time.
- Forgot the remote pairing password? Run `agentj passphrase reset --phone` on the computer and confirm on a paired phone; every remote stays paired.
- The To-do card now gives every item an owner (Agent J or you), a start and a due time, and can show only Agent J's or only your items; Agent J checks what is due when it wakes and marks its own items done.
- Drops shorter than 5 seconds reconnect quietly; the disconnected notice appears only when the link is really gone. Emergency repair results can be closed and fold away after about 30 seconds.
- Several instances on one computer share one speech model; later instances need no download.
- An empty seat card on the account page shows its own host binding code and binds to that seat.
- The install assistant can choose a free OpenCode model.
- The To-do, Notes and Automation cards are on by default and can be switched off in Settings; card titles and icons use one light ink.
- Daily AI News is switched on in each computer's own settings and goes to the email of the person using that seat.
- A command a card's + puts in the message box now ends with a space, so it shows as a command link at once.
- In a desktop browser, ⌘V or Ctrl+V with no field selected puts the text at the end of the message box and a picture into the attachments; in another field, or with a menu or Settings open, pasting works there as usual.
- `agentj provider ark` now sets up the Volcano Engine Ark Agent Plan by default (`--plan agent`); the Coding Plan stays as `--plan coding`.
- Settings has a connection dot: green when connected, red when not, and a short "Connection restored" notice when it comes back.
- Fewer messages between friends are blocked by mistake; when one is blocked, the sender sees why and how to rephrase it.
- After a drop of a few seconds (for example the Agent J host restarting), an attachment that was uploading carries on to the end; no need to pick it again.
- The post-upgrade self-check names the check that blocked it; waiting for a phone message no longer counts as a failure.
- Stuck billing changes are verified and unlocked automatically; binding failures say what really happened; the Agent name length rule is the same in the command line, account page and docs.
- Admin commands now run in the background: the phone keeps sending and receiving while a long one runs, the card shows it is running, and when it ends the card closes and the chat keeps a short result.
- Content library citations prefer the archive page.
- Reply option buttons centre their text vertically; outside links in Agent replies use a clearer colour.
- On iPhone and iPad the top status bar and bottom area stay dark after the computer connects instead of following the state colour.
- The description lines on /model and similar option cards wrap inside the card, even on narrow screens and large text.
- The robot logo at the top left now takes the colour of the current state (blue while working), including a theme's own state colours; a logo you chose yourself stays as it is.
- Computers with OpenCode 2.x can now switch to OpenCode; when the check before the switch fails, it shows OpenCode's own error line.
- 每一页右上角的搜索只搜这一页：卡片页原地筛选记录，设置页点结果直接打开对应项并闪一下。
- 管理员命令卡片新增一个可选开关（默认关，要你自己勾选）：打开后 10 分钟内，同一个文件夹里同样格式的只读命令不再重复询问；shell 和脚本类命令每次都问，随时可以收回。
- 忘了遥控器配对密码时，在电脑上运行 `agentj passphrase reset --phone`，用已配对的手机确认后重设，所有遥控器保持配对。
- 待办卡升级：每条有责任人（Agent J 或你）、开始和截止，卡上可以只看 Agent J 负责或你负责的；Agent J 醒来先查该做的事，做完自己标完成。
- 断开 5 秒内会悄悄重连，只有真的连不上才弹出提示；紧急修复的结果可以关闭，约 30 秒后自动收起。
- 同一台电脑上的多个实例共用一份语音模型，后装的实例不用再下载。
- 账户页的空席位卡片直接显示主机绑定码，绑定到这个席位。
- 安装助手可以选择 OpenCode 的免费模型。
- 待办、记事本、自动化三张官方卡默认打开，不需要可以在设置里关掉；卡片标题和图标统一用浅色字。
- Daily AI News 在每台主机自己的设置里开关，发到这个席位使用者自己的邮箱。
- 卡片「+」放进输入框的命令自动带一个空格，命令链接立即显示。
- 在电脑浏览器里不用先点输入框，直接按 ⌘V 或 Ctrl+V，文字接到输入框末尾、图片进附件；在别的输入框里或菜单、设置开着时照常粘到那里。
- `agentj provider ark` 默认接入火山方舟 Agent Plan（`--plan agent`），原来的 Coding Plan 用 `--plan coding` 保留。
- 设置页有一个连接圆点：绿色是连着，红色是断开，恢复后会提示「已恢复连接」。
- 好友之间的消息误拦更少；真被拦下时，发送方能看到原因和改写建议。
- 断线几秒后恢复（比如电脑上的 Agent J 重启），正在上传的附件会接着传完，不用重选。
- 升级后的自检报告写明是哪一项没通过；等待手机消息不再算作失败。
- 卡住的账单操作会自动核对并解锁；绑定失败时说出真正的原因；Agent 名字长度规则在命令行、账户页和文档里保持一致。
- 管理员命令改在后台执行：长命令跑着时手机照常收发消息，卡片显示「执行中」，执行完卡片关闭、聊天里留一条结果摘要。
- 内容库引用优先指向存档页。
- 回复里的选项按钮文字上下居中；Agent 回复里的外部链接换成更醒目的颜色。
- iPhone 和 iPad 上顶部状态栏和底部区域连上电脑后也始终保持深色，不再跟着状态变色。
- /model 等选项卡片里的说明文字在卡片内自动换行，窄屏和大字号下也不再超出卡片。
- 左上角的机器人 logo 跟着当前状态变色（工作中是蓝色），换了主题配色也跟着变；自己换的 logo 保持原样。
- 用 OpenCode 2.x 的电脑现在可以切换到 OpenCode；切换前的检查没通过时，会附上 OpenCode 自己的一行报错。

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
