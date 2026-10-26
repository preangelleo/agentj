# Agent J 0.25.13a1 release notes

Host 0.25.13a1 / install 0.27.13; phone-web scope.
Phone parity must pass --strict. Full Android/Telegram parity is incomplete.
Full Android/Telegram parity is incomplete.
No APK is included in this release. Android source is for review, not a released app.

Publication status: local preparation only. Live site verification is DEFERRED until
the public commit and tag are pushed, the site is deployed, and cold installation passes.

Release order: C applies app D1 0039_seat_grants.sql, 0040_prepaid_seats.sql and 0041_seat_transfer_host.sql in this order before the compatible Dashboard Worker; then B phone web (per-seat phone addresses) before A signed host/site/downloads, in the same window. No relay change, no feedback D1 change. Preserve0036 revocation state,0037 action UV data,0038 checkout pending records and local security journals permanently. Never replay migrations or restore an older database snapshot. Use compatible rollback only, otherwise repair forward. Native Mac M1 and physical iOS acceptance remain separate. Local preparation only; no push or deployment authorized.
## Changes

- Each seat has its own phone address, m.agentj.app/<seat id>, so remotes of several Agent J hosts no longer mix; the old address still works.
- Seats can be paid once for 1, 3 or 12 months ($200 per seat for 12 months) with Alipay, WeChat Pay or a card; a reminder comes before the end.
- A remote can set up its Face ID after another remote that already has one approves it, even if its old key was lost; no more browser QR sheet.
- A seat can be moved to another Agent J host from the remote's Settings; the remotes follow once you confirm.
- New approval level "Follow your AI coding tool": only spending money, deleting your data, unbinding or moving a seat and sending a key off this computer still need Face ID; everything else follows the tool's own rules.
- When the terminal stops at a menu, the phone lists the same options and one tap picks one; model names always carry their version.
- Reply cards and the reader can forward straight to WeChat, which receives clean plain text.
- The heartbeat prompt adds checks of scheduled-task logs, friends and stand-in replies, and can be edited full screen or reset in Settings.
- Confirmations follow the action and who started it: a reversible action you tap on your remote runs directly, the Agent's gets a long-press card, anything irreversible needs Face ID; dispatching a sub-agent no longer asks.
- When Claude Code's safety check stops a message, the phone offers four choices (continue on another model, retry, edit and resend, new conversation) instead of sending you back to the computer.
- Every remote creates its own passkey on the device and Settings shows its state; switching AI coding tools hands the context straight to the new tool.
- Your WeChat, private Telegram and allowlisted mail handled by your stand-in can use the main conversation's tools and approvals; channel pages show the sender's words.
- Status hints, run cards and the remote badge follow the current state and your custom theme; after an upgrade the host checks itself and stays quiet when all is well.
- Mail app passwords are no longer lost after an upgrade; a key card filled on one device closes on every other device.
- Forwarding mail yourself from your remote no longer asks for a passkey.
- A WeChat stand-in on Codex no longer keeps sending the fallback reply; failures say why and what to do next.
- OpenCode scratch folders are cleaned up and leave nothing in your home folder.
- Face ID setup refused on Android or Huawei devices now says why and what to do; setting it again on the same device replaces the old key.
- 每个席位有自己的手机网址 m.agentj.app/<席位号>，多台 Agent J 主机的遥控器互不串门；原来的网址照常能用。
- 席位可以预付：一次付清 1、3 或 12 个月（12 个月每席位 $200），支持支付宝、微信支付和银行卡；到期前会提醒续费。
- 遥控器的 Face ID 可以由另一台已设好的遥控器批准后在本机设好，旧钥匙丢了也能重新设，不再出现浏览器扫码框。
- 可以在遥控器的设置里把席位转移到另一台 Agent J 主机，遥控器确认后自动跟过去。
- 新增「跟随 AI 编程工具（Harness）」审批档：只有花钱、删除你的数据、解绑或转移席位、密钥发到本机以外这四类还要 Face ID，其余按 AI 编程工具自己的规则。
- 终端停在菜单时，手机上会照原样列出选项，点一下就能选；模型名统一带版本号。
- 回复卡和阅读器可以直接转发到微信，微信收到的是排好版的纯文字。
- 心跳提示新增定时任务日志、好友和分身复核三项检查，可在设置里全屏编辑或恢复默认。
- 确认按「动作 × 谁发起」分级：你在遥控器上亲手点的可撤回操作直接执行，Agent 发起的出长按卡，不可撤回的一律 Face ID；派子 Agent 不再弹卡。
- Claude Code 的安全检查拦下消息时，手机上给出四个选择（换模型继续、原话重试、改一改再发、开新对话），不用回电脑。
- 每台遥控器在本机建自己的通行密钥，设置页能看到本机状态；切换 AI 编程工具时，交接内容直接带给新工具。
- 你的微信、私聊 Telegram 和白名单邮件交给分身处理时，能用主对话的工具和审批；渠道消息页显示对方原话。
- 状态提示、运行卡和遥控器角标跟随当前状态和自定义主题变色；升级完成后由主机自检，没问题就不打扰。
- 升级后邮件授权码不再丢失；同一个密钥卡在一台设备上填好后，其他设备上的同一张卡一起收起。
- 你在遥控器上亲手转发邮件不再要求通行密钥。
- 微信分身选 Codex 时不再一直回兜底消息，失败时会说明原因和下一步。
- OpenCode 临时目录用完即清，不再在主目录留下残留。
- 安卓和华为设备设置 Face ID 被拒时，会说明原因和下一步；同一台设备再设一次会替换旧钥匙。

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
