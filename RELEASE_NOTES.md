# Agent J 0.25.4a1 release notes

Host 0.25.4a1 / install 0.27.4; phone-web scope.
Phone parity must pass --strict. Full Android/Telegram parity is incomplete.
Full Android/Telegram parity is incomplete.
No APK is included in this release. Android source is for review, not a released app.

Publication status: local preparation only. Live site verification is DEFERRED until
the public commit and tag are pushed, the site is deployed, and cold installation passes.

On failure restore v0.25.3a1 host/site/phone and the captured Dashboard Worker version. Release order: C (Dashboard support, knowledge and installation guidance; no D1 migration), then A signed host/site/downloads then B phone web immediately after. No relay change. The eleven phone action icons are already live. Preserve local Notebook migration backups and newly edited notes on downgrade. Local preparation only; publication needs separate authorization. Physical iPhone/Android and native WSL acceptance are not claimed.
## Changes

- Export a local diagnostic summary of fixed statuses and log event counts, for the owner to review before sharing.
- Check WSL browser libraries and download reachability, and choose a first local draft task using setup estimates from installed skill receipts.
- Stop the previous task manually from a queued message. A quiet notice appears after three minutes both queued and without earlier-task output; reloading starts a new observation.
- After upgrades, repair missing core references in the existing work root, try the existing reconnection path and check again. Remaining tasks are distinguished from actual upgrade failures.
- Support and bundled instructions now prefer agentj update apply, using the current same-seat install command only when the old updater is unavailable or fails.
- Includes the eleven already-live outline action icons, preserving handlers and accessible labels.
- Same-owner friend tasks no longer submit trusted host forwarding instructions as untrusted review input. Successful handovers notify the owner, and active older requests can recover their reply mapping.
- Owner-facing replies retain public-chain-shaped addresses and transaction identifiers. Explicit credential labels and known key formats remain masked; an encoding alone does not prove a value is public.
- Ambiguous Claude mise/asdf installs default to the highest executable stable numeric version while preserving the ambiguity notice and explicit selections.
- Release tooling handles timezone-aware deadlines across midnight, checks its install interpreter early and keeps runtime evidence outside the repository.
- Retain the already-live iPhone water-transition and bottom-safe-area colour fix during the combined release.
- Recover cleanly from a failed first OpenCode start. The next message can retry, stale connection cleanup cannot overwrite a replacement session, and rescue reports recovery only when the AI adapter is ready.
- 新增本机诊断导出，只包含固定状态和日志事件计数，主人审阅后自行决定是否分享。
- 新增 WSL 浏览器依赖与下载入口预检，以及按已装技能依赖估计的入门顺序和首个本机草稿任务。
- 排队消息可手动停下上一件；排队且前一件连续三分钟没有新输出时，显示静默提示，刷新后重新观察。
- 升级后补齐已有工作根的身份引用、尝试恢复连接，再复检；区分真正升级失败与升级成功后的待办。
- 客服、安装说明和内置升级技能统一先用 agentj update apply；旧更新器失败才重跑同一席位的当前安装命令。
- 保留已上线的十一处线条图标，原有按钮行为和可访问标签不变。
- 修复同主人好友任务投递时误审主机转交提示、缺少主人通知，以及部分有效旧请求无法回报的问题。
- 主人通道保留公开链上编码形状的地址和交易标识，显式凭据、私钥标签及已知密钥格式仍遮挡；单凭形状无法证明一个值公开。
- Claude mise/asdf 多版本缺省选择最高可执行的稳定数字版本，并保留歧义提示；明确选择仍优先。
- 发布工具支持带时区的跨午夜截止时间，提前检查安装解释器，并将运行期证据保存在仓外。
- 保留已上线的 iPhone 水位过渡与底部安全区连续色修复，避免整版升级回退网页。
- 修复 OpenCode 首次启动失败后残留死进程的问题；下一条消息可重试，旧连接清理不会覆盖新会话，救援状态只在 AI 适配器真正就绪后恢复。

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
