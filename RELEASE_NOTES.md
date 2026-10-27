# Agent J 0.25.14a1 release notes

Host 0.25.14a1 / install 0.27.14; phone-web scope.
Phone parity must pass --strict. Full Android/Telegram parity is incomplete.
Full Android/Telegram parity is incomplete.
No APK is included in this release. Android source is for review, not a released app.

Publication status: local preparation only. Live site verification is DEFERRED until
the public commit and tag are pushed, the site is deployed, and cold installation passes.

Release order: C Dashboard support knowledge only, no D1 migration (app 0039-0041 are live since 0.25.13 and are never replayed); then A signed host/site/downloads, immediately followed by B phone web, in the same window. No relay change, no feedback D1 change. Preserve0036 revocation state,0037 action UV data,0038 checkout pending records,0041 seat transfer records and local security journals permanently. Never replay migrations or restore an older database snapshot. Use compatible rollback only, otherwise repair forward. Qualified by the affected suites of the six hotfix tickets under the owner's 10-27 rule (no fresh full parity, full host or real-install acceptance run for this release). Local preparation only; no push or deployment authorized.
## Changes

- The third approval level is now called "Full access": Agent J shows no approval cards and the AI coding tool the Agent J host starts runs with its widest permissions (your config files are not changed); only spending money, deleting your data, unbinding or moving a seat, sending a key away, and attempts to change Agent J itself still need Face ID.
- The forward destination is saved on the Agent J host and shared by every remote: change it on one and the others follow at once.
- When Chrome on a Mac already has Touch ID set up, an approval opens Touch ID directly instead of the phone QR sheet.
- A Full access level you confirmed earlier keeps working after the upgrade, no reconfirmation; if its signature lapses (the remote was unpaired or got a new key), Settings shows "Not in force" with the reason and tells you once how to restore it.
- Work sent to the background (background sub-tasks, scheduled tasks, orders) no longer shows cards; the main Agent reviews the result. The four dangerous kinds still need Face ID.
- A remote's Face ID set up before the upgrade is recognised automatically and shows "Set up"; when no other remote can approve, you can approve on the Agent J host with the pairing password instead of getting stuck.
- Your stand-in on WeChat, private Telegram and allowlisted mail has the main conversation's rights: it makes images and edits files itself, sends files back as attachments, and its replies also show in the Web App; only real secrets are held back.
- Final wording for the seat-transfer dialog; icon buttons without borders.
- ToolSearch, Skill and other loader tools are no longer refused as unknown actions; a truly unknown action names the tool and asks you to report it.
- WeChat forwarding has no time limit, so a two-day-old conversation still goes through; only a refusal by WeChat asks you to send one message in ClawBot first.
- The forward button is back to the mirrored reply arrow.
- Bash in the main session is no longer refused without a card: a Full access level with a valid signature runs it, a lapsed one shows the card as usual and says why.
- 审批档位第三档改名「全放行」：Agent J 不再弹审批卡，Agent J 主机自己启动的 AI 编程工具以最大权限运行（不写你的配置文件）；只有花钱、删除你的数据、解绑或转移席位、密钥外发，以及有人想改动 Agent J 本身时仍要 Face ID。
- 转发去向保存在 Agent J 主机上，所有遥控器共用：一台改了，其他遥控器立刻跟着变。
- Mac 上的 Chrome 已设好 Touch ID 时，审批直接调起 Touch ID，不再弹手机扫码框。
- 以前签名确认过的「全放行」升级后直接沿用，不用重新确认；签名失效（遥控器解绑或换了钥匙）时，设置页显示「未生效」和原因，并一次性告诉你怎么恢复。
- 派到后台的活（后台子任务、定时任务、派单）不再弹卡，结果由主 Agent 审核；四类危险操作仍要 Face ID。
- 升级前已经设好的遥控器 Face ID 会被自动认出，显示「已设好」；没有其他遥控器能批准时，可以在 Agent J 主机上用配对密码批准，不会卡死。
- 微信、Telegram 私聊和白名单邮件里的分身与主对话同权：能自己出图、改文件，文件作为附件原样发回，回复同步显示在网页里；对外只拦真正的机密。
- 席位转移弹窗文案定稿；图标按钮去掉边框。
- ToolSearch、Skill 等加载类工具不再被当成未知动作拒绝；真遇到未知动作时会写出工具名并提示反馈。
- 微信转发不再有时间限制，两天前的对话也能发；只有微信平台拒收时才提示先在 ClawBot 里发一句。
- 转发按钮恢复成「回复」的镜像弯箭头。
- 主会话的 Bash 不再出现「没卡就被拒」：签名有效的全放行直接执行，失效时照常弹卡并说明原因。

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
