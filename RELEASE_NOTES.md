# Agent J 0.25.3a1 release notes

Host 0.25.3a1 / install 0.27.3; phone-web scope.
Phone parity must pass --strict. Full Android/Telegram parity is incomplete.
Full Android/Telegram parity is incomplete.
No APK is included in this release. Android source is for review, not a released app.

Publication status: local preparation only. Live site verification is DEFERRED until
the public commit and tag are pushed, the site is deployed, and cold installation passes.

On failure restore v0.25.2a1 host/site/phone and the captured Dashboard Worker version. Release order: C (Dashboard Worker only, no D1 migration), then A signed host/site/downloads then B phone web immediately after. No relay change. P472/P466/P473 web and account changes are already live. Keep the byte-exact Notebook migration backup when downgrading; do not overwrite newly edited notes. Local preparation only; publication needs separate authorization. Physical iPhone/Android acceptance is not claimed.
## Changes

- Notebook titles, content and dates are separate, with editable titles. Existing notes are backed up before migration.
- Reorder all five composer tools, including Expand full screen. Valid existing orders are preserved.
- Check the provider used by OpenCode main, small and sub-agent models, then confirm aligning them. Ark and Bailian keys placed in the wrong row can be checked and moved with confirmation, without overwriting a different key.
- Exit recognized Claude Code menus and see the terminal-reported quota reset time. Choices that may incur charges still require your explicit selection.
- Install and discover OpenCode 2 while retaining OpenCode 1. Context meters use a consistent 200K fallback when a free model does not report its capacity.
- Fenced agents can ask the host service to run connection checks and support submissions without reading host credentials or disabling isolation.
- Theme exports omit unchanged official logos. Existing official images keep following status colours; custom artwork is preserved.
- A healthy service after a rollback now shows a clear rollback warning instead of a lingering recovery failure.
- More conservative masking for multiline, structured and mixed-script terminal credentials. Device diagnostics omit pairing keys, and busy-turn queries have bounded waits.
- Includes recent web fixes: fixed Noir iPhone status paint, keyboard-safe full-screen editing, restored interactive sheets, To-do icons, status-coloured robots and the full remote list.
- Short status notices use a circle; longer explanations remain fully readable in the same position. Reply copying briefly shows a tick, and reader copying keeps the reader open.
- Account dialogs fit the viewport. Paid-seat reconciliation and duplicate installation reservations are handled more reliably.
- Reply text and diagram labels use Lotion. Controls, chart shapes, borders and the reader keep their colours.
- 记事本把标题、正文和日期分开，标题可以单独编辑；旧笔记迁移前先保留原文件备份。
- 附件栏的文件、图片、相机、麦克风和「展开全屏」五项都能排序，合法的旧排序会保留。
- 设置页可核对 OpenCode 主模型、小模型和子 Agent 的服务商，并确认统一到主模型服务商。方舟、百炼 Key 填错行时，可核对套餐后确认挪行，不覆盖另一把不同的 Key。
- Claude Code 的回退和其它已识别菜单可退出，额度菜单显示原生恢复时间。额外用量、升级等可能收费的选项仍由你自己确认。
- 支持安装和发现 OpenCode 2，同时保留 OpenCode 1；免费模型缺少容量资料时，上下文水位使用统一的 200K 回退上限。
- 隔离中的 Agent 可让主机后台完成连接自检和客服反馈，无需读取主机凭据或关闭隔离。
- 导出主题不再复制官方 logo；旧主题中原样的官方图片继续随状态着色，自定义图片保留。
- 升级已回滚且服务恢复正常时，清楚显示回滚警告，不再继续误报恢复失败。
- 网页终端更保守地遮挡多行、混合字符和结构化凭据；设备诊断不再输出配对密钥，忙碌状态查询有等待上限。
- 同步近期网页修复：iPhone 顶部固定 Noir，键盘和全屏编辑不挡按钮；交互选择恢复原有面板；待办图标、机器人状态色和完整遥控器列表修复。
- 简短状态用圆形提示，长说明在同处完整显示；回复拷贝只短暂显示勾号，阅读页拷贝后继续阅读。
- 账号弹窗随视口调整，席位数量核对和安装预留去重更可靠。
- 正文和图表标签统一为 Lotion 文字色，按钮、图形、边框与阅读页保留原色。

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
